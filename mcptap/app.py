"""Aiohttp application setup, request handlers, and lifecycle management."""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import (  # type: ignore
    ClientSession,
    web,
)

from mcptap.config_reloader import (
    ConfigReloader,
    reload_env_and_propagate,
    reload_intercept,
    reload_per_model_config,
    reload_tool_hook,
)
from mcptap.credits_checker import CreditsCheckerTask
from mcptap.encrypted_replay import (
    ReplayContext,
    build_route_fingerprint,
    log_replay_sanitization,
)
from mcptap.http_utils import filtered_headers, upstream_path
from mcptap.log_retention import LogRetentionTask
from mcptap.log_store import LogStore, record_from_response
from mcptap.mcp_intercept import MCPInterceptor, load_intercept_config
from mcptap.pareto_data import ParetoDataTask
from mcptap.response_flow import handle_responses_with_intercept
from mcptap.responses import response_json_from_raw
from mcptap.rewrite import load_per_model_config, rewrite_json_payload
from mcptap.session import SessionTracker
from mcptap.settings import LOGGER, settings
from mcptap.tool_hook import ToolHookGateway
from mcptap.upstream import (
    create_client_session,
    emit_buffered_response,
    forward_rewritten,
    passthrough,
    post_upstream_buffered_with_replay_retry,
)


async def health(_request: web.Request) -> web.Response:
    intercept: MCPInterceptor = _request.app["mcp_intercept"]
    if intercept.enabled and intercept._config is not None:
        server = intercept._config
        intercept_info: Optional[Dict[str, Any]] = {
            "mcp_command": server["mcp_command"],
            "mcp_args": server["mcp_args"],
            "mcp_cwd": server.get("mcp_cwd"),
            "mappings": [
                {
                    "expose_as": t.expose_as,
                    "mcp_tool": t.mcp_tool,
                    "resolved": t.resolved_parameters is not None,
                }
                for t in intercept.tools
            ],
        }
    else:
        intercept_info = None

    per_model_config: Dict[str, Dict[str, Any]] = _request.app["per_model_config"]

    return web.json_response(
        {
            "status": "ok",
            "upstream": settings.upstream_base_url,
            "forced_model": settings.model,
            "forced_provider": settings.openrouter_provider or None,
            "provider_fallbacks_disabled": settings.openrouter_disable_provider_fallbacks,
            "use_chat_completions": settings.use_chat_completions,
            "mcp_intercept": intercept_info,
            "per_model_config": per_model_config,
            "use_tool_hook": {
                "enabled": bool(settings.use_tool_hook),
                "hook_script": settings.use_tool_hook or None,
                "timeout": settings.use_tool_hook_timeout,
            },
        }
    )


async def proxy(request: web.Request) -> web.StreamResponse:
    session: ClientSession = request.app["client_session"]
    intercept: MCPInterceptor = request.app["mcp_intercept"]
    per_model_config: Dict[str, Dict[str, Any]] = request.app["per_model_config"]
    hook_gateway: ToolHookGateway = request.app["hook_gateway"]
    session_tracker: SessionTracker = request.app["session_tracker"]
    path = upstream_path(request.path)
    target_url = settings.upstream_base_url + path
    if request.query_string:
        target_url += "?" + request.query_string

    request_headers = filtered_headers(request.headers)
    request_headers["Authorization"] = f"Bearer {settings.api_key}"
    raw_body = await request.read()

    content_type = request.headers.get("Content-Type", "").lower()
    payload: Optional[Dict[str, Any]] = None
    if "application/json" in content_type and raw_body:
        try:
            candidate = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            candidate = None
        if isinstance(candidate, dict) and "model" in candidate:
            payload = candidate

    if payload is None:
        return await passthrough(request, session, target_url, request_headers, raw_body)

    try:
        original_model, forced_model, reasoning_effort = rewrite_json_payload(
            request,
            payload,
            intercept,
            per_model_config,
        )
    except Exception as exc:
        LOGGER.exception(exc)
        return web.json_response(
            {"error": {"message": str(exc), "type": "proxy_upstream_error"}},
            status=502,
        )

    client_wanted_stream = bool(payload.get("stream"))

    LOGGER.info(
        "%s %s model=%r -> %r reasoning_effort=%r provider=%r stream=%s intercept=%s hook=%s",
        request.method,
        request.path_qs,
        original_model,
        forced_model,
        reasoning_effort,
        settings.openrouter_provider or "OpenRouter-selected",
        client_wanted_stream,
        intercept.enabled,
        hook_gateway.enabled,
    )

    is_responses_call = request.method == "POST" and path.rstrip("/").endswith("/responses")
    replay_context: Optional[ReplayContext] = None
    if is_responses_call:
        session_id = request.headers.get("session-id", "").strip() or "default"
        route_fingerprint = build_route_fingerprint(
            path=path,
            model=str(payload.get("model", "")),
            upstream_base_url=settings.upstream_base_url,
            upstream_provider=settings.upstream_provider,
            api_key=settings.api_key,
            provider=payload.get("provider"),
        )
        replay_plan = await session_tracker.prepare_encrypted_replay(
            session_id,
            route_fingerprint,
            payload,
        )
        log_replay_sanitization(
            LOGGER,
            session_id=session_id,
            removal=replay_plan.removal,
            previous_route_fingerprint=replay_plan.previous_route_fingerprint,
            route_fingerprint=route_fingerprint,
            reason="model_route_changed",
        )
        replay_context = ReplayContext(
            session_id=session_id,
            route_fingerprint=route_fingerprint,
            previous_route_fingerprint=replay_plan.previous_route_fingerprint,
            retry_on_404=replay_plan.retry_on_404,
        )

    if not is_responses_call or not (intercept.enabled or hook_gateway.enabled):
        log_store: Optional[LogStore] = request.app.get("log_store")
        start_time = time.time()
        if replay_context is not None and replay_context.retry_on_404:
            (
                status,
                response_headers,
                response_raw,
                response_body_json,
                removal,
            ) = await post_upstream_buffered_with_replay_retry(
                session,
                path,
                request_headers,
                payload,
                client_wanted_stream,
            )
            log_replay_sanitization(
                LOGGER,
                session_id=replay_context.session_id,
                removal=removal,
                previous_route_fingerprint=replay_context.previous_route_fingerprint,
                route_fingerprint=replay_context.route_fingerprint,
                reason="upstream_404_retry",
            )
            if 200 <= status < 300:
                await session_tracker.record_encrypted_replay(
                    replay_context.session_id,
                    replay_context.route_fingerprint,
                    payload,
                    response_body_json,
                )
            if log_store and log_store.enabled:
                record_from_response(
                    log_store,
                    request_body=payload,
                    response_raw=response_raw,
                    response_body_json=response_body_json,
                    session_id=replay_context.session_id,
                    model=forced_model,
                    provider=settings.openrouter_provider or settings.upstream_provider,
                    status_code=status,
                    request_path=path,
                    stream=client_wanted_stream,
                    start_time=start_time,
                )
            return await emit_buffered_response(
                request,
                status=status,
                headers=response_headers,
                raw=response_raw,
            )

        resp, response_raw = await forward_rewritten(
            request,
            session,
            target_url,
            request_headers,
            payload,
        )
        if replay_context is not None and 200 <= resp.status < 300:
            await session_tracker.record_encrypted_replay(
                replay_context.session_id,
                replay_context.route_fingerprint,
                payload,
                response_json_from_raw(response_raw, client_wanted_stream),
            )
        if log_store and log_store.enabled and hasattr(resp, "status"):
            record_from_response(
                log_store,
                request_body=payload,
                response_raw=response_raw,
                response_body_json=response_json_from_raw(response_raw, client_wanted_stream),
                session_id=request.headers.get("session-id", "").strip() or "default",
                model=forced_model,
                provider=settings.openrouter_provider or settings.upstream_provider,
                status_code=resp.status,
                request_path=path,
                stream=client_wanted_stream,
                start_time=start_time,
            )
        return resp

    return await handle_responses_with_intercept(
        request,
        session,
        path,
        request_headers,
        payload,
        intercept,
        client_wanted_stream,
        hook_gateway,
        session_tracker,
        log_store=request.app.get("log_store"),
        replay_context=replay_context,
    )


async def _create_client_session_startup(app: web.Application) -> None:
    app["client_session"] = create_client_session()


async def _close_client_session(app: web.Application) -> None:
    await app["client_session"].close()


async def _start_mcp_intercept(app: web.Application) -> None:
    intercept: MCPInterceptor = app["mcp_intercept"]
    if not intercept.enabled:
        LOGGER.info("MCP intercept disabled (MCP_TAP_INTERCEPT_YAML is empty)")
        return
    try:
        await intercept.start()
    except Exception as exc:
        LOGGER.exception("Failed to start MCP intercept; continuing without interception: %s", exc)


async def _stop_mcp_intercept(app: web.Application) -> None:
    intercept: MCPInterceptor = app.get("mcp_intercept")
    if intercept is not None:
        await intercept.stop()


async def _start_config_reloader(app: web.Application) -> None:
    reloader: ConfigReloader = app["config_reloader"]
    reloader.attach(
        app=app,
        on_env_reload=lambda: reload_env_and_propagate(app),
        on_intercept_reload=lambda: reload_intercept(app),
        on_per_model_reload=lambda: reload_per_model_config(app),
        on_tool_hook_reload=lambda: reload_tool_hook(app),
    )
    reloader.start()


async def _stop_config_reloader(app: web.Application) -> None:
    reloader: ConfigReloader = app.get("config_reloader")
    if reloader is not None:
        await reloader.stop()


async def _close_log_store(app: web.Application) -> None:
    log_store: Optional[LogStore] = app.get("log_store")
    if log_store is not None:
        log_store.close()


_PID_FILE = Path(settings.per_session_dir).parent / "proxy.pid"


def _write_pid_file() -> None:
    """Write current PID to /tmp/mcptap/proxy.pid for LD_PRELOAD discovery.

    The file_block .so library reads this to locate the MCPTap process and
    fetch its listen address from /proc/<pid>/environ.
    """
    try:
        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PID_FILE.write_text(str(os.getpid()))
    except OSError as exc:
        LOGGER.warning("Failed to write PID file %s: %s", _PID_FILE, exc)


async def _remove_pid_file(app: web.Application) -> None:
    """Remove the PID file on shutdown."""
    try:
        _PID_FILE.unlink(missing_ok=True)
    except OSError as exc:
        LOGGER.warning("Failed to remove PID file %s: %s", _PID_FILE, exc)


async def _start_log_retention(app: web.Application) -> None:
    log_store: Optional[LogStore] = app.get("log_store")
    if log_store is None or not log_store.enabled:
        return
    task = LogRetentionTask(log_store)
    app["log_retention"] = task
    task.start()


async def _stop_log_retention(app: web.Application) -> None:
    task: Optional[LogRetentionTask] = app.get("log_retention")
    if task is not None:
        await task.stop()


async def _start_pareto_data(app: web.Application) -> None:
    task = ParetoDataTask()
    app["pareto_data"] = task
    task.start()


async def _stop_pareto_data(app: web.Application) -> None:
    task: Optional[ParetoDataTask] = app.get("pareto_data")
    if task is not None:
        await task.stop()


async def _start_credits_checker(app: web.Application) -> None:
    checker: Optional[CreditsCheckerTask] = app.get("credits_checker")
    if checker is not None:
        checker.start()


async def _stop_credits_checker(app: web.Application) -> None:
    checker: Optional[CreditsCheckerTask] = app.get("credits_checker")
    if checker is not None:
        await checker.stop()


def build_app() -> web.Application:
    app = web.Application(client_max_size=100 * 1024 * 1024)
    try:
        intercept_config = load_intercept_config()
    except Exception as exc:
        LOGGER.exception("Invalid MCP_TAP_INTERCEPT_YAML; disabling intercept (%s)", exc)
        intercept_config = None

    try:
        per_model_config = load_per_model_config()
    except Exception as exc:
        LOGGER.exception("Invalid MCP_TAP_PER_MODEL_YAML; disabling per-model config (%s)", exc)
        per_model_config = None

    session_tracker = SessionTracker()
    hook_gateway = ToolHookGateway(session_tracker)
    log_store = LogStore(settings.log_db_path)
    try:
        log_store.migrate()
    except Exception as exc:
        LOGGER.exception("Failed to migrate log database; disabling log store: %s", exc)
        log_store = LogStore(settings.log_db_path, enabled=False)

    if not per_model_config:
        LOGGER.info("Per-model config disabled (MCP_TAP_PER_MODEL_YAML is empty)")
    if not hook_gateway.enabled:
        LOGGER.info("Tool hook disabled (MCP_TAP_USE_TOOL_HOOK is empty)")

    app["mcp_intercept"] = MCPInterceptor(intercept_config)
    app["per_model_config"] = per_model_config
    app["session_tracker"] = session_tracker
    app["hook_gateway"] = hook_gateway
    app["log_store"] = log_store
    app["config_reloader"] = ConfigReloader()
    app["credits_checker"] = CreditsCheckerTask(log_store)
    app.on_startup.append(_create_client_session_startup)
    app.on_startup.append(_start_mcp_intercept)
    app.on_startup.append(_start_config_reloader)
    app.on_startup.append(_start_log_retention)
    app.on_startup.append(_start_pareto_data)
    app.on_startup.append(_start_credits_checker)
    app.on_cleanup.append(_remove_pid_file)
    app.on_cleanup.append(_stop_log_retention)
    app.on_cleanup.append(_stop_pareto_data)
    app.on_cleanup.append(_stop_credits_checker)
    app.on_cleanup.append(_stop_config_reloader)
    app.on_cleanup.append(_stop_mcp_intercept)
    app.on_cleanup.append(_close_client_session)
    app.on_cleanup.append(_close_log_store)
    from mcptap.log_api import handle_log_detail, handle_logs_list, serve_logs_page

    app.router.add_get("/health", health)
    app.router.add_get("/api/logs", handle_logs_list)
    app.router.add_get("/api/logs/{log_id}", handle_log_detail)
    app.router.add_get("/ui/logs", serve_logs_page)
    app.router.add_route("*", "/{tail:.*}", proxy)
    return app


def main() -> None:
    _write_pid_file()
    LOGGER.info(
        "Listening on http://%s:%s; upstream=%s; forced_model=%s; forced_plan_mode_model=%s; forced_provider=%s",
        settings.listen_host,
        settings.listen_port,
        settings.upstream_base_url,
        settings.model,
        settings.plan_mode_model,
        settings.openrouter_provider or "OpenRouter-selected",
    )
    web.run_app(
        build_app(),
        host=settings.listen_host,
        port=settings.listen_port,
        access_log=None,
        handle_signals=True,
    )
