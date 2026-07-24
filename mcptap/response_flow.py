"""Responses processor — coordinates the MCP intercept loop and tool-hook gateway.

This module owns the multi-turn conversation loop: it talks to upstream in a
loop, resolving intercepted tool calls locally until the model returns a final
response with no intercepted calls. When the tool-call hook is configured,
batches of client function calls are intercepted: a synthetic get_goal call is
returned to the client, and on the next request the hook script decides
whether to allow or block.
"""

import contextlib
import copy
import json
import time
from typing import Any, Callable, Dict, List, Optional

from aiohttp import (  # type: ignore
    ClientError,
    ClientSession,
    web,
)

from mcptap.file_block import write_blocklist
from mcptap.log_store import LogStore, record_from_response
from mcptap.mcp_intercept import MCPInterceptor
from mcptap.responses import (
    apply_tool_call_updates,
    build_hook_error_response,
    build_sse_from_response,
    build_synthetic_tool_response,
    extract_client_tool_calls,
    extract_get_goal_result,
    extract_intercepted_calls,
    extract_usage_total_tokens,
    has_client_tool_calls,
    has_intercepted_calls,
    re_serialize_response,
    strip_synthetic_get_goal,
)
from mcptap.session import SessionTracker
from mcptap.settings import LOGGER, settings
from mcptap.tool_hook import PendingState, ToolHookGateway
from mcptap.upstream import post_upstream_buffered


async def _emit_buffered_response(
    request: web.Request,
    status: int,
    headers: Dict[str, str],
    raw: bytes,
) -> web.StreamResponse:
    response_headers = dict(headers)
    response_headers.pop("Content-Encoding", None)
    response_headers.pop("Content-Length", None)
    if raw and not response_headers.get("Content-Type"):
        response_headers["Content-Type"] = "application/json"
    response = web.StreamResponse(status=status, headers=response_headers)
    await response.prepare(request)
    with contextlib.suppress(ConnectionResetError, BrokenPipeError):
        await response.write(raw)
        await response.write_eof()
    LOGGER.info("%s %s -> HTTP %s", request.method, request.path_qs, status)
    return response


async def handle_responses_with_intercept(
    request: web.Request,
    session: ClientSession,
    path: str,
    request_headers: Dict[str, str],
    payload: Dict[str, Any],
    intercept: MCPInterceptor,
    client_wanted_stream: bool,
    hook_gateway: ToolHookGateway,
    session_tracker: SessionTracker,
    log_store: Optional[LogStore] = None,
) -> web.StreamResponse:
    """Talk to upstream in a loop, resolving intercepted tool calls locally
    until the model returns a final response with no intercepted calls.

    When MCP_TAP_USE_TOOL_HOOK is configured, batches of client function calls
    are intercepted: a synthetic get_goal call is returned to the client, and
    on the next request the hook script decides whether to allow or block.
    """

    session_id = request.headers.get("session-id", "").strip() or "default"
    forced_model = payload.get("model", settings.model)

    session_info = await session_tracker.track_request(request, forced_model)
    session_id = session_info["session_id"]

    request_start_time = time.time()
    provider = settings.openrouter_provider or settings.upstream_provider

    pending_state = await hook_gateway.get_pending(session_id) if hook_gateway.enabled else None

    working_payload = copy.deepcopy(payload)
    working_payload.pop("stream", None)

    def _ensure_input_list(pl: Optional[Dict[str, Any]] = None) -> List[Any]:
        target = pl if pl is not None else working_payload
        raw_input = target.get("input")
        if raw_input is None:
            target["input"] = []
        elif isinstance(raw_input, str):
            target["input"] = [{"role": "user", "content": [{"type": "input_text", "text": raw_input}]}]
        elif not isinstance(raw_input, list):
            target["input"] = [raw_input]
        return target["input"]

    if pending_state is not None:
        return await _handle_hook_decision(
            request,
            session,
            path,
            request_headers,
            working_payload,
            intercept,
            client_wanted_stream,
            hook_gateway,
            session_tracker,
            session_id,
            pending_state,
            _ensure_input_list,
            log_store=log_store,
            request_start_time=request_start_time,
            provider=provider,
        )

    last_status = 200
    last_headers: Dict[str, str] = {}
    last_body_raw: bytes = b""

    intercept_names = intercept.tool_names() if intercept.enabled else set()

    for iteration in range(settings.intercept_max_iterations):
        try:
            status, resp_headers, raw, body_json = await post_upstream_buffered(
                session,
                path,
                request_headers,
                working_payload,
                stream=client_wanted_stream,
            )
        except (ClientError, OSError) as exc:
            LOGGER.exception("Upstream request failed: %s", exc)
            return web.json_response(
                {
                    "error": {
                        "message": "OpenRouter proxy could not reach the upstream API",
                        "type": "proxy_upstream_error",
                    }
                },
                status=502,
            )

        last_status = status
        last_headers = resp_headers
        last_body_raw = raw

        if status >= 400:
            LOGGER.warning(
                "Upstream returned HTTP %s during intercept loop iteration=%d",
                status,
                iteration,
            )
            break

        if body_json is None:
            LOGGER.warning("Upstream response could not be parsed during intercept loop; forwarding as-is")
            break

        tokens = extract_usage_total_tokens(body_json)
        if tokens > 0:
            await session_tracker.add_usage(session_id, tokens)

        record_from_response(
            log_store,
            request_body=working_payload,
            response_raw=raw,
            response_body_json=body_json,
            session_id=session_id,
            model=forced_model,
            provider=provider,
            status_code=status,
            request_path=path,
            stream=client_wanted_stream,
            start_time=request_start_time,
        )

        has_intercepted = has_intercepted_calls(body_json, intercept_names)
        has_client = has_client_tool_calls(body_json, intercept_names)

        if has_intercepted:
            hits = extract_intercepted_calls(body_json, intercept_names)
            LOGGER.info(
                "Intercept iteration=%d hits=%d names=%r",
                iteration,
                len(hits),
                [name for _, _, name, _ in hits],
            )

            _ensure_input_list()
            working_payload.pop("previous_response_id", None)
            for out_item in body_json.get("output") or []:
                if isinstance(out_item, dict):
                    working_payload["input"].append(out_item)

            for _, call_id, name, arguments in hits:
                try:
                    parsed_args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
                    if not isinstance(parsed_args, dict):
                        parsed_args = {}
                except json.JSONDecodeError:
                    parsed_args = {}
                output_text = await intercept.call(name, parsed_args)
                working_payload["input"].append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output_text,
                    }
                )

            continue

        if has_client and hook_gateway.enabled:
            client_calls = extract_client_tool_calls(body_json, intercept_names)
            if client_calls:
                used_tokens = await session_tracker.get_usage(session_id)
                used_time = await session_tracker.get_elapsed_seconds(session_id)

                if settings.use_tool_hook_synthetic_tool:
                    state = PendingState(
                        session_id=session_id,
                        saved_status=status,
                        saved_headers=resp_headers,
                        saved_raw=raw,
                        saved_body_json=body_json,
                        client_tool_calls=client_calls,
                        get_goal_result={},
                        forced_model=forced_model,
                        used_tokens=used_tokens,
                        used_time_seconds=used_time,
                    )
                    await hook_gateway.set_pending(session_id, state)

                    LOGGER.info(
                        "Tool hook: deferring %d client tool calls for session=%s; returning synthetic %s",
                        len(client_calls),
                        session_id,
                        settings.use_tool_hook_synthetic_tool,
                    )

                    synthetic = build_synthetic_tool_response(
                        forced_model,
                        settings.use_tool_hook_synthetic_tool,
                    )
                    if client_wanted_stream:
                        sse_raw = build_sse_from_response(synthetic)
                        return await _emit_buffered_response(request, status=200, headers={}, raw=sse_raw)
                    else:
                        return await _emit_buffered_response(
                            request,
                            status=200,
                            headers={"Content-Type": "application/json"},
                            raw=json.dumps(synthetic, ensure_ascii=False).encode("utf-8"),
                        )
                else:
                    state = PendingState(
                        session_id=session_id,
                        saved_status=status,
                        saved_headers=resp_headers,
                        saved_raw=raw,
                        saved_body_json=body_json,
                        client_tool_calls=client_calls,
                        get_goal_result={},
                        forced_model=forced_model,
                        used_tokens=used_tokens,
                        used_time_seconds=used_time,
                    )
                    return await _handle_direct_hook(
                        request,
                        session,
                        path,
                        request_headers,
                        working_payload,
                        intercept,
                        client_wanted_stream,
                        hook_gateway,
                        session_tracker,
                        session_id,
                        state,
                        _ensure_input_list,
                        log_store=log_store,
                        request_start_time=request_start_time,
                        provider=provider,
                    )

        break
    else:
        LOGGER.warning(
            "Intercept loop reached MCP_TAP_INTERCEPT_MAX_ITERATIONS=%d without a final answer",
            settings.intercept_max_iterations,
        )

    return await _emit_buffered_response(
        request,
        status=last_status,
        headers=last_headers,
        raw=last_body_raw,
    )


async def _handle_direct_hook(
    request: web.Request,
    session: ClientSession,
    path: str,
    request_headers: Dict[str, str],
    working_payload: Dict[str, Any],
    intercept: MCPInterceptor,
    client_wanted_stream: bool,
    hook_gateway: ToolHookGateway,
    session_tracker: SessionTracker,
    session_id: str,
    pending_state: PendingState,
    ensure_input_list_fn: Callable,
    log_store: Optional[LogStore] = None,
    request_start_time: float = 0.0,
    provider: str = "",
) -> web.StreamResponse:
    """Run the hook immediately without injecting a synthetic tool call.

    The hook receives the tool calls and session context directly, with no
    synthetic tool result.  On ``allow``, the saved upstream response is
    returned (optionally with file-block instructions).  On ``block``, the
    block message is fed to the model in a follow-up upstream request.
    """

    try:
        decision = await hook_gateway.run_hook(pending_state)
    except RuntimeError as exc:
        LOGGER.error("Tool hook error for session=%s: %s", session_id, exc)
        error_resp = build_hook_error_response(str(exc), pending_state.forced_model)
        if client_wanted_stream:
            sse_raw = build_sse_from_response(error_resp)
            return await _emit_buffered_response(request, status=200, headers={}, raw=sse_raw)
        else:
            return await _emit_buffered_response(
                request,
                status=200,
                headers={"Content-Type": "application/json"},
                raw=json.dumps(error_resp, ensure_ascii=False).encode("utf-8"),
            )

    action = decision.get("action")

    if action == "allow":
        blocked_files = decision.get("blocked_files", [])
        updated_tool_calls = decision.get("updated_tool_calls", [])
        LOGGER.info(
            "Tool hook allowed tool calls for session=%s (blocked_files=%d, updated_tool_calls=%d)",
            session_id,
            len(blocked_files),
            len(updated_tool_calls),
        )

        if blocked_files:
            write_blocklist(session_id, blocked_files)

        response_raw = pending_state.saved_raw
        if updated_tool_calls:
            modified = apply_tool_call_updates(pending_state.saved_body_json, updated_tool_calls)
            if modified:
                response_raw = re_serialize_response(pending_state.saved_body_json, client_wanted_stream)
                LOGGER.info(
                    "Tool hook applied %d tool call updates for session=%s",
                    len(updated_tool_calls),
                    session_id,
                )

        return await _emit_buffered_response(
            request,
            status=pending_state.saved_status,
            headers=pending_state.saved_headers,
            raw=response_raw,
        )

    block_message = decision.get("message", "Tool calls blocked by hook")
    LOGGER.info("Tool hook blocked tool calls for session=%s: %s", session_id, block_message)

    ensure_input_list_fn(working_payload)

    for out_item in pending_state.saved_body_json.get("output") or []:
        if isinstance(out_item, dict):
            working_payload["input"].append(out_item)

    existing_instructions = working_payload.get("instructions", "") or ""
    block_instruction = f"\n\n[TOOL CALL BLOCKED] {block_message}"
    working_payload["instructions"] = existing_instructions + block_instruction

    working_payload.pop("previous_response_id", None)

    try:
        status, resp_headers, raw, body_json = await post_upstream_buffered(
            session,
            path,
            request_headers,
            working_payload,
            stream=client_wanted_stream,
        )
    except (ClientError, OSError) as exc:
        LOGGER.exception("Upstream request failed after block: %s", exc)
        return web.json_response(
            {
                "error": {
                    "message": "OpenRouter proxy could not reach the upstream API",
                    "type": "proxy_upstream_error",
                }
            },
            status=502,
        )

    if body_json:
        tokens = extract_usage_total_tokens(body_json)
        if tokens > 0:
            await session_tracker.add_usage(session_id, tokens)

    record_from_response(
        log_store,
        request_body=working_payload,
        response_raw=raw,
        response_body_json=body_json,
        session_id=session_id,
        model=pending_state.forced_model,
        provider=provider,
        status_code=status,
        request_path=path,
        stream=client_wanted_stream,
        start_time=request_start_time,
    )

    return await _emit_buffered_response(
        request,
        status=status,
        headers=resp_headers,
        raw=raw,
    )


async def _handle_hook_decision(
    request: web.Request,
    session: ClientSession,
    path: str,
    request_headers: Dict[str, str],
    working_payload: Dict[str, Any],
    intercept: MCPInterceptor,
    client_wanted_stream: bool,
    hook_gateway: ToolHookGateway,
    session_tracker: SessionTracker,
    session_id: str,
    pending_state: PendingState,
    ensure_input_list_fn: Callable,
    log_store: Optional[LogStore] = None,
    request_start_time: float = 0.0,
    provider: str = "",
) -> web.StreamResponse:
    """Handle the request that follows a synthetic get_goal call.

    The client has executed get_goal and the result is in the input items.
    MCPTap extracts the result, runs the hook, and either returns the saved
    response (allow) or feeds the block message to the model (block).
    """

    get_goal_result = extract_get_goal_result(working_payload)
    pending_state.get_goal_result = get_goal_result

    try:
        decision = await hook_gateway.run_hook(pending_state)
    except RuntimeError as exc:
        LOGGER.error("Tool hook error for session=%s: %s", session_id, exc)
        await hook_gateway.clear_pending(session_id)
        error_resp = build_hook_error_response(str(exc), pending_state.forced_model)
        if client_wanted_stream:
            sse_raw = build_sse_from_response(error_resp)
            return await _emit_buffered_response(request, status=200, headers={}, raw=sse_raw)
        else:
            return await _emit_buffered_response(
                request,
                status=200,
                headers={"Content-Type": "application/json"},
                raw=json.dumps(error_resp, ensure_ascii=False).encode("utf-8"),
            )

    action = decision.get("action")

    if action == "allow":
        blocked_files = decision.get("blocked_files", [])
        updated_tool_calls = decision.get("updated_tool_calls", [])
        LOGGER.info(
            "Tool hook allowed tool calls for session=%s (blocked_files=%d, updated_tool_calls=%d)",
            session_id,
            len(blocked_files),
            len(updated_tool_calls),
        )
        await hook_gateway.clear_pending(session_id)

        if blocked_files:
            write_blocklist(session_id, blocked_files)

        response_raw = pending_state.saved_raw
        if updated_tool_calls:
            modified = apply_tool_call_updates(pending_state.saved_body_json, updated_tool_calls)
            if modified:
                response_raw = re_serialize_response(pending_state.saved_body_json, client_wanted_stream)
                LOGGER.info(
                    "Tool hook applied %d tool call updates for session=%s",
                    len(updated_tool_calls),
                    session_id,
                )

        return await _emit_buffered_response(
            request,
            status=pending_state.saved_status,
            headers=pending_state.saved_headers,
            raw=response_raw,
        )

    block_message = decision.get("message", "Tool calls blocked by hook")
    LOGGER.info("Tool hook blocked tool calls for session=%s: %s", session_id, block_message)
    await hook_gateway.clear_pending(session_id)

    ensure_input_list_fn(working_payload)
    working_payload["input"] = strip_synthetic_get_goal(working_payload["input"])

    for out_item in pending_state.saved_body_json.get("output") or []:
        if isinstance(out_item, dict):
            working_payload["input"].append(out_item)

    existing_instructions = working_payload.get("instructions", "") or ""
    block_instruction = f"\n\n[TOOL CALL BLOCKED] {block_message}"
    working_payload["instructions"] = existing_instructions + block_instruction

    working_payload.pop("previous_response_id", None)

    try:
        status, resp_headers, raw, body_json = await post_upstream_buffered(
            session,
            path,
            request_headers,
            working_payload,
            stream=client_wanted_stream,
        )
    except (ClientError, OSError) as exc:
        LOGGER.exception("Upstream request failed after block: %s", exc)
        return web.json_response(
            {
                "error": {
                    "message": "OpenRouter proxy could not reach the upstream API",
                    "type": "proxy_upstream_error",
                }
            },
            status=502,
        )

    if body_json:
        tokens = extract_usage_total_tokens(body_json)
        if tokens > 0:
            await session_tracker.add_usage(session_id, tokens)

    record_from_response(
        log_store,
        request_body=working_payload,
        response_raw=raw,
        response_body_json=body_json,
        session_id=session_id,
        model=pending_state.forced_model,
        provider=provider,
        status_code=status,
        request_path=path,
        stream=client_wanted_stream,
        start_time=request_start_time,
    )

    return await _emit_buffered_response(
        request,
        status=status,
        headers=resp_headers,
        raw=raw,
    )
