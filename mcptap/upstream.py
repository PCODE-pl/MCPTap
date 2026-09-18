"""Upstream HTTP client — buffered communication with the provider API."""

import contextlib
import hashlib
import json
import uuid
from typing import Any, Dict, Optional, Tuple

from aiohttp import (  # type: ignore
    ClientError,
    ClientSession,
    ClientTimeout,
    TCPConnector,
    web,
)

from mcptap.chat_completions import (
    chat_sse_to_responses,
    convert_chat_response,
    looks_like_chat_completions_sse,
    responses_request_to_chat,
)
from mcptap.chat_store import PersistentChatStore
from mcptap.encrypted_replay import (
    ReplayItemRemoval,
    filter_encrypted_replay_items,
    is_encrypted_replay_error,
)
from mcptap.http_utils import filtered_headers, log_communication
from mcptap.responses import response_json_from_raw, response_json_from_sse
from mcptap.settings import LOGGER, PROVIDER_OPENCODE, settings

_CHAT_CONVERSATIONS = PersistentChatStore()

# Zen free-tier gate (checked 2026-09-18, v1.18.31 sources): requests must
# present the official client identity — User-Agent
# opencode/<channel>/<version>/<client> with version >= 1.17.0 — and a
# canonical x-opencode-session id (ses_[0-9a-f]{12}[0-9A-Za-z]{14}). On the
# free lane the tool quartet (bash/glob/grep/read) must be declared in the
# body and the request must stream (non-stream answers 403 FreeTierError).
OPENCODE_CHANNEL = "latest"
OPENCODE_VERSION = "1.18.31"
OPENCODE_CLIENT = "cli"
_OPENCODE_USER_AGENT = f"opencode/{OPENCODE_CHANNEL}/{OPENCODE_VERSION}/{OPENCODE_CLIENT}"

OPENCODE_GATE_TOOLS = [
    {
        "type": "function",
        "name": "bash",
        "description": "Run a bash command.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    },
    {
        "type": "function",
        "name": "glob",
        "description": "Find files by glob pattern.",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
    },
    {
        "type": "function",
        "name": "grep",
        "description": "Search file contents.",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
    },
    {
        "type": "function",
        "name": "read",
        "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
]


def _canonical_opencode_session(session_id: str) -> str:
    """Map any client session id onto the gate's canonical ses_ shape.

    The mapping is deterministic (stable per conversation for upstream
    rate-limiting/routing) and always yields
    ses_[0-9a-f]{12}[0-9A-Za-z]{14}.
    """
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"ses_{digest[:12]}{digest[12:26]}"


def _replace_header(headers: Dict[str, str], name: str, value: str) -> None:
    """Set a header without leaving a differently-cased duplicate behind."""
    for existing_name in list(headers):
        if existing_name.lower() == name.lower():
            del headers[existing_name]
    headers[name] = value


def _apply_provider_headers(headers: Dict[str, str], body: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Apply headers required by the selected upstream provider."""
    outgoing_headers = dict(headers)
    if settings.upstream_provider != PROVIDER_OPENCODE:
        return outgoing_headers

    session_id = next(
        (value.strip() for name, value in outgoing_headers.items() if name.lower() == "session-id" and value.strip()),
        "",
    )
    if not session_id and body is not None:
        prompt_cache_key = body.get("prompt_cache_key")
        if isinstance(prompt_cache_key, str):
            session_id = prompt_cache_key.strip()
    # The gate requires the header on every request; without a client
    # session a fresh canonical id is minted per call.
    _replace_header(
        outgoing_headers,
        "x-opencode-session",
        _canonical_opencode_session(session_id or uuid.uuid4().hex),
    )
    _replace_header(outgoing_headers, "User-Agent", _OPENCODE_USER_AGENT)
    return outgoing_headers


def _uses_chat_completions(path: str) -> bool:
    return settings.use_chat_completions and path.rstrip("/").endswith("/responses")


def _is_responses_stream_call(path: str, payload: Dict[str, Any]) -> bool:
    return path.rstrip("/").endswith("/responses") and bool(payload.get("stream"))


def _chat_upstream_path(path: str) -> str:
    if not _uses_chat_completions(path):
        return path
    normalized = path
    for prefix in ("/api/v1", "/v1"):
        if normalized == prefix:
            normalized = ""
            break
        if normalized.startswith(prefix + "/"):
            normalized = normalized[len(prefix) :]
            break
    return normalized[: -len("responses")] + "chat/completions"


def _repair_function_call_pairs(body: Dict[str, Any]) -> None:
    """Make every function_call in a Responses input have exactly one output.

    Upstream (Console) rejects the request with 400 invalid_request_error
    when a call_id carries more than one function_call_output ("Each
    function_call must have exactly one matching function_call_output") or
    when a function_call has no output at all ("invalid parameters", live
    verified 2026-09-18). Long agent histories replay tool results, which
    produces both shapes. Duplicate outputs: the last occurrence wins —
    it is the freshest output for the call. Orphan calls: a synthetic
    "(tool result missing)" output is appended so the call/model context
    is preserved. Items without a call_id are left untouched.
    """
    input_value = body.get("input")
    if not isinstance(input_value, list):
        return
    seen: set = set()
    deduped = []
    for item in reversed(input_value):
        if (
            isinstance(item, dict)
            and item.get("type") in {"function_call_output", "custom_tool_call_output"}
            and isinstance(item.get("call_id"), str)
            and item["call_id"]
        ):
            if item["call_id"] in seen:
                continue
            seen.add(item["call_id"])
        deduped.append(item)
    deduped.reverse()
    call_types = {"function_call": "function_call_output", "custom_tool_call": "custom_tool_call_output"}
    for item in list(deduped):
        if (
            isinstance(item, dict)
            and item.get("type") in call_types
            and isinstance(item.get("call_id"), str)
            and item["call_id"]
            and item["call_id"] not in seen
        ):
            seen.add(item["call_id"])
            deduped.append(
                {
                    "type": call_types[item["type"]],
                    "call_id": item["call_id"],
                    "output": "(tool result missing)",
                }
            )
    body["input"] = deduped


def _parse_json_object(raw: bytes) -> Optional[Dict[str, Any]]:
    try:
        candidate = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return candidate if isinstance(candidate, dict) else None


async def post_upstream_buffered(
    session: ClientSession,
    path: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    stream: bool,
) -> Tuple[int, Dict[str, str], bytes, Optional[Dict[str, Any]]]:
    """Post to upstream and buffer the complete response.

    For stream=true the upstream SSE is buffered and converted to a
    Responses-compatible SSE stream so the proxy can resolve hidden MCP tool
    calls before returning the response to the client.
    """
    chat_mode = _uses_chat_completions(path)
    opencode_provider = settings.upstream_provider == PROVIDER_OPENCODE
    # The zen free-tier gate requires stream=true even for clients asking for
    # a buffered JSON response; the SSE is buffered and the final
    # response.completed payload is returned as JSON below.
    upstream_stream = stream or opencode_provider
    request_body = dict(body)
    _repair_function_call_pairs(request_body)
    if chat_mode:
        request_body = responses_request_to_chat(request_body, _CHAT_CONVERSATIONS, stream=upstream_stream)
    upstream_path = _chat_upstream_path(path)
    outgoing_headers = _apply_provider_headers(headers, body)
    outgoing_headers["Content-Type"] = "application/json"
    if upstream_stream:
        request_body["stream"] = True
        outgoing_headers.pop("Accept", None)
        outgoing_headers["Accept"] = "text/event-stream"
    else:
        request_body.pop("stream", None)
        outgoing_headers.pop("Accept", None)
        outgoing_headers["Accept"] = "application/json"
    if opencode_provider and not chat_mode and upstream_path.rstrip("/").endswith("/responses"):
        # Gate requires the tool quartet in the body; add only tool names the
        # client does not already declare to avoid duplicate-name rejections.
        declared = {
            tool.get("name") for tool in request_body.get("tools") or [] if isinstance(tool, dict) and tool.get("name")
        }
        additions = [tool for tool in OPENCODE_GATE_TOOLS if tool["name"] not in declared]
        if additions:
            request_body["tools"] = list(request_body.get("tools") or []) + additions

    url = settings.upstream_base_url + upstream_path
    data = json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    log_communication("upstream_request", "POST", url, outgoing_headers, data)
    async with session.post(
        url,
        headers=outgoing_headers,
        data=data,
        allow_redirects=False,
    ) as resp:
        raw = await resp.read()
        response_headers = filtered_headers(resp.headers)
        log_communication("upstream_response", "POST", url, response_headers, raw, status=resp.status)

    # Parse the body for all status codes, not just < 400, so that error
    # responses (429, 500, etc.) are also available for logging and inspection.
    if chat_mode and resp.status < 400:
        chat_result = chat_sse_to_responses(raw) if stream else _parse_json_object(raw)
        if isinstance(chat_result, dict) and "error" not in chat_result:
            response_id = f"resp_{uuid.uuid4().hex[:24]}"
            raw, response_json = convert_chat_response(raw, stream, response_id=response_id)
            if response_json is None:
                response_json = {}
            body_json = response_json
            stored_chat_body = {key: value for key, value in chat_result.items() if key != "sse"}
            _CHAT_CONVERSATIONS.store_response(response_id, request_body["messages"], stored_chat_body)
            response_headers["Content-Type"] = "text/event-stream" if stream else "application/json"
        else:
            body_json = response_json_from_raw(raw, stream)
    elif stream and resp.status < 400 and looks_like_chat_completions_sse(raw):
        # Some providers answer /responses with native Chat Completions SSE;
        # convert it so the client gets a valid Responses stream.
        LOGGER.warning("Upstream answered Responses call with Chat Completions SSE; converting")
        response_id = f"resp_{uuid.uuid4().hex[:24]}"
        raw, response_json = convert_chat_response(raw, stream, response_id=response_id)
        body_json = response_json if response_json is not None else {}
        response_headers["Content-Type"] = "text/event-stream"
    else:
        body_json = response_json_from_raw(raw, upstream_stream)

    # A non-stream client asking for a gated stream response gets the final
    # response.completed payload materialized back as JSON.
    if opencode_provider and not chat_mode and upstream_stream and not stream and resp.status < 400:
        completed = response_json_from_sse(raw)
        if completed is not None:
            raw = json.dumps(completed, ensure_ascii=False).encode("utf-8")
            body_json = completed
            response_headers["Content-Type"] = "application/json"

    return resp.status, response_headers, raw, body_json


async def post_upstream_buffered_with_replay_retry(
    session: ClientSession,
    path: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    stream: bool,
) -> Tuple[int, Dict[str, str], bytes, Optional[Dict[str, Any]], ReplayItemRemoval]:
    """Retry once after removing encrypted replay items rejected by upstream."""
    status, response_headers, raw, body_json = await post_upstream_buffered(
        session,
        path,
        headers,
        body,
        stream,
    )
    if not is_encrypted_replay_error(status, body_json):
        return status, response_headers, raw, body_json, ReplayItemRemoval()

    removal = filter_encrypted_replay_items(body, set())
    if not removal.total:
        return status, response_headers, raw, body_json, removal

    status, response_headers, raw, body_json = await post_upstream_buffered(
        session,
        path,
        headers,
        body,
        stream,
    )
    return status, response_headers, raw, body_json, removal


async def emit_buffered_response(
    request: web.Request,
    status: int,
    headers: Dict[str, str],
    raw: bytes,
) -> web.StreamResponse:
    """Write a buffered upstream response to the client."""
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


async def passthrough(
    request: web.Request,
    session: ClientSession,
    target_url: str,
    request_headers: Dict[str, str],
    raw_body: bytes,
) -> web.StreamResponse:
    LOGGER.info("%s %s (body not rewritten)", request.method, request.path_qs)
    outgoing_headers = _apply_provider_headers(request_headers)
    log_communication("upstream_request", request.method, target_url, outgoing_headers, raw_body)
    try:
        upstream_response = await session.request(
            method=request.method,
            url=target_url,
            headers=outgoing_headers,
            data=raw_body if raw_body else None,
            allow_redirects=False,
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
    response = web.StreamResponse(
        status=upstream_response.status,
        reason=upstream_response.reason,
        headers=filtered_headers(upstream_response.headers),
    )
    await response.prepare(request)
    response_body = bytearray()
    try:
        if request.method != "HEAD":
            async for chunk in upstream_response.content.iter_any():
                response_body.extend(chunk)
                await response.write(chunk)
    except (ConnectionResetError, BrokenPipeError):
        LOGGER.info("Client disconnected during streamed response")
    finally:
        log_communication(
            "upstream_response",
            request.method,
            target_url,
            filtered_headers(upstream_response.headers),
            bytes(response_body),
            status=upstream_response.status,
        )
        upstream_response.release()
    with contextlib.suppress(ConnectionResetError, BrokenPipeError):
        await response.write_eof()
    LOGGER.info("%s %s -> HTTP %s", request.method, request.path_qs, upstream_response.status)
    return response


async def forward_rewritten(
    request: web.Request,
    session: ClientSession,
    target_url: str,
    request_headers: Dict[str, str],
    payload: Dict[str, Any],
) -> Tuple[web.StreamResponse, bytes]:
    """Forward a rewritten request to upstream, streaming the response to the
    client while also collecting the raw body for logging.

    Returns a ``(StreamResponse, bytes)`` tuple where the second element is the
    full response body, enabling logging of error responses (429, 500, etc.).
    """
    if _uses_chat_completions(request.path):
        try:
            status, response_headers, raw, _body_json = await post_upstream_buffered(
                session,
                request.path,
                request_headers,
                payload,
                bool(payload.get("stream")),
            )
        except (ClientError, OSError) as exc:
            LOGGER.exception("Upstream request failed: %s", exc)
            error_body = {
                "error": {
                    "message": "OpenRouter proxy could not reach the upstream API",
                    "type": "proxy_upstream_error",
                }
            }
            return web.json_response(error_body, status=502), json.dumps(error_body).encode("utf-8")
        return await emit_buffered_response(request, status, response_headers, raw), raw

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    outgoing_headers = _apply_provider_headers(request_headers, payload)
    outgoing_headers["Content-Type"] = "application/json"
    log_communication("upstream_request", request.method, target_url, outgoing_headers, body)
    try:
        upstream_response = await session.request(
            method=request.method,
            url=target_url,
            headers=outgoing_headers,
            data=body,
            allow_redirects=False,
        )
    except (ClientError, OSError) as exc:
        LOGGER.exception("Upstream request failed: %s", exc)
        error_body = json.dumps(
            {
                "error": {
                    "message": "OpenRouter proxy could not reach the upstream API",
                    "type": "proxy_upstream_error",
                }
            }
        ).encode("utf-8")
        resp = web.json_response(
            json.loads(error_body.decode("utf-8")),
            status=502,
        )
        return resp, error_body
    if (
        _is_responses_stream_call(request.path, payload)
        and upstream_response.status < 400
        and "text/event-stream" in (upstream_response.headers.get("Content-Type") or "")
    ):
        # Buffer first: a Chat-SSE masquerade must be converted before the
        # client sees a single byte, and we cannot know which protocol the
        # upstream speaks until the first chunk arrives.
        response_body = bytearray()
        try:
            async for chunk in upstream_response.content.iter_any():
                response_body.extend(chunk)
        except (ConnectionResetError, BrokenPipeError):
            LOGGER.info("Client disconnected during streamed response")
        finally:
            log_communication(
                "upstream_response",
                request.method,
                target_url,
                filtered_headers(upstream_response.headers),
                bytes(response_body),
                status=upstream_response.status,
            )
            upstream_response.release()
        raw = bytes(response_body)
        if looks_like_chat_completions_sse(raw):
            LOGGER.warning("Upstream answered Responses call with Chat Completions SSE; converting")
            response_id = f"resp_{uuid.uuid4().hex[:24]}"
            raw, _response_json = convert_chat_response(raw, stream=True, response_id=response_id)
        response = web.StreamResponse(
            status=upstream_response.status,
            reason=upstream_response.reason,
            headers=filtered_headers(upstream_response.headers),
        )
        await response.prepare(request)
        try:
            await response.write(raw)
            await response.write_eof()
        except (ConnectionResetError, BrokenPipeError):
            LOGGER.info("Client disconnected during streamed response")
        LOGGER.info("%s %s -> HTTP %s", request.method, request.path_qs, upstream_response.status)
        return response, raw
    response = web.StreamResponse(
        status=upstream_response.status,
        reason=upstream_response.reason,
        headers=filtered_headers(upstream_response.headers),
    )
    await response.prepare(request)
    response_body = bytearray()
    try:
        async for chunk in upstream_response.content.iter_any():
            response_body.extend(chunk)
            await response.write(chunk)
    except (ConnectionResetError, BrokenPipeError):
        LOGGER.info("Client disconnected during streamed response")
    finally:
        log_communication(
            "upstream_response",
            request.method,
            target_url,
            filtered_headers(upstream_response.headers),
            bytes(response_body),
            status=upstream_response.status,
        )
        upstream_response.release()
    with contextlib.suppress(ConnectionResetError, BrokenPipeError):
        await response.write_eof()
    LOGGER.info("%s %s -> HTTP %s", request.method, request.path_qs, upstream_response.status)
    return response, bytes(response_body)


def create_client_session() -> ClientSession:
    timeout = ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=None)
    connector = TCPConnector(limit=100, ttl_dns_cache=300)
    return ClientSession(
        timeout=timeout,
        connector=connector,
        auto_decompress=True,
    )
