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

from mcptap.http_utils import filtered_headers, log_communication
from mcptap.responses import response_json_from_sse
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

    body_json: Optional[Dict[str, Any]] = None
    if resp.status < 400:
        if stream:
            body_json = response_json_from_sse(raw)
        else:
            try:
                candidate = json.loads(raw.decode("utf-8"))
                if isinstance(candidate, dict):
                    body_json = candidate
            except (UnicodeDecodeError, json.JSONDecodeError):
                body_json = None
    return resp.status, response_headers, raw, body_json


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
) -> web.StreamResponse:
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


def create_client_session() -> ClientSession:
    timeout = ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=None)
    connector = TCPConnector(limit=100, ttl_dns_cache=300)
    return ClientSession(
        timeout=timeout,
        connector=connector,
        auto_decompress=True,
    )
