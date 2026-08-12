"""Upstream HTTP client — buffered communication with the provider API."""

import contextlib
import json
from typing import Any, Dict, Optional, Tuple

from aiohttp import (  # type: ignore
    ClientError,
    ClientSession,
    ClientTimeout,
    TCPConnector,
    web,
)

from mcptap.encrypted_replay import (
    ReplayItemRemoval,
    filter_encrypted_replay_items,
    is_encrypted_replay_error,
)
from mcptap.http_utils import filtered_headers, log_communication
from mcptap.responses import response_json_from_raw
from mcptap.settings import LOGGER, settings


async def post_upstream_buffered(
    session: ClientSession,
    path: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    stream: bool,
) -> Tuple[int, Dict[str, str], bytes, Optional[Dict[str, Any]]]:
    """Post to upstream and buffer the complete response.

    For stream=true we preserve the exact upstream SSE bytes for the client,
    but also parse response.completed so the proxy can resolve hidden MCP tool
    calls before deciding whether to replay that stream.
    """
    request_body = dict(body)
    outgoing_headers = dict(headers)
    outgoing_headers["Content-Type"] = "application/json"
    if stream:
        request_body["stream"] = True
        outgoing_headers.pop("Accept", None)
        outgoing_headers["Accept"] = "text/event-stream"
    else:
        request_body.pop("stream", None)
        outgoing_headers.pop("Accept", None)
        outgoing_headers["Accept"] = "application/json"

    url = settings.upstream_base_url + path
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
    body_json = response_json_from_raw(raw, stream)
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
    log_communication("upstream_request", request.method, target_url, request_headers, raw_body)
    try:
        upstream_response = await session.request(
            method=request.method,
            url=target_url,
            headers=request_headers,
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
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    outgoing_headers = dict(request_headers)
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
