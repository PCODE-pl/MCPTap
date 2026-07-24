"""Aiohttp application setup, request handlers, and lifecycle management."""

import json
import time
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
from mcptap.http_utils import filtered_headers, upstream_path
from mcptap.log_store import LogStore, record_from_response
from mcptap.mcp_intercept import MCPInterceptor, load_intercept_config
from mcptap.response_flow import handle_responses_with_intercept
from mcptap.rewrite import load_per_model_config, rewrite_json_payload
from mcptap.session import SessionTracker
from mcptap.settings import LOGGER, settings
from mcptap.tool_hook import ToolHookGateway
from mcptap.upstream import create_client_session, forward_rewritten, passthrough


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
    if not is_responses_call or not (intercept.enabled or hook_gateway.enabled):
        log_store: Optional[LogStore] = request.app.get("log_store")
        start_time = time.time()
        resp = await forward_rewritten(
            request,
            session,
            target_url,
            request_headers,
            payload,
        )
        if log_store and log_store.enabled and hasattr(resp, "status"):
            record_from_response(
                log_store,
                request_body=payload,
                response_raw=b"",
                response_body_json=None,
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
    app.on_startup.append(_create_client_session_startup)
    app.on_startup.append(_start_mcp_intercept)
    app.on_startup.append(_start_config_reloader)
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
