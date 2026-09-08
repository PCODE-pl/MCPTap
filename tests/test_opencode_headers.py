"""Tests for OpenCode-specific upstream request headers."""

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer

from mcptap.upstream import passthrough, post_upstream_buffered


@pytest.mark.asyncio
async def test_opencode_headers_identify_session_and_client(monkeypatch):
    received_headers = {}

    async def handler(request):
        received_headers.update(request.headers)
        return web.json_response({"id": "resp_1", "model": "m", "output": []})

    server = TestServer(web.Application())
    server.app.router.add_post("/v1/responses", handler)
    client = TestClient(server)
    await client.start_server()
    try:
        from mcptap.settings import settings

        monkeypatch.setattr(settings, "upstream_provider", "opencode")
        monkeypatch.setattr(settings, "upstream_base_url", str(server.make_url("/v1")))
        monkeypatch.setattr(settings, "api_key", "test-key")
        await post_upstream_buffered(
            client.session,
            "/responses",
            {"session-id": "session-123", "User-Agent": "hermes/test"},
            {"model": "m", "input": "Hello"},
            False,
        )
    finally:
        await client.close()

    assert received_headers["x-opencode-session"] == "session-123"
    assert received_headers["User-Agent"] == "opencode/mcp-tap"


@pytest.mark.asyncio
async def test_passthrough_sends_opencode_headers(monkeypatch):
    received_headers = {}

    async def upstream_handler(request):
        received_headers.update(request.headers)
        return web.Response(body=b"upstream response")

    upstream_server = TestServer(web.Application())
    upstream_server.app.router.add_post("/raw", upstream_handler)
    await upstream_server.start_server()

    upstream_session = ClientSession()
    proxy_app = web.Application()

    async def proxy_handler(request):
        from mcptap.settings import settings

        monkeypatch.setattr(settings, "upstream_provider", "opencode")
        return await passthrough(
            request,
            upstream_session,
            str(upstream_server.make_url("/raw")),
            {"session-id": "session-123", "User-Agent": "hermes/test"},
            b"request body",
        )

    proxy_app.router.add_post("/proxy", proxy_handler)
    proxy_client = TestClient(TestServer(proxy_app))
    await proxy_client.start_server()
    try:
        response = await proxy_client.post("/proxy", data=b"ignored")
        assert response.status == 200
        assert await response.read() == b"upstream response"
    finally:
        await proxy_client.close()
        await upstream_session.close()
        await upstream_server.close()

    assert received_headers["x-opencode-session"] == "session-123"
    assert received_headers["User-Agent"] == "opencode/mcp-tap"


@pytest.mark.asyncio
async def test_non_opencode_headers_are_not_rewritten(monkeypatch):
    received_headers = {}

    async def handler(request):
        received_headers.update(request.headers)
        return web.json_response({"id": "resp_1", "model": "m", "output": []})

    server = TestServer(web.Application())
    server.app.router.add_post("/v1/responses", handler)
    client = TestClient(server)
    await client.start_server()
    try:
        from mcptap.settings import settings

        monkeypatch.setattr(settings, "upstream_provider", "openrouter")
        monkeypatch.setattr(settings, "upstream_base_url", str(server.make_url("/v1")))
        monkeypatch.setattr(settings, "api_key", "test-key")
        await post_upstream_buffered(
            client.session,
            "/responses",
            {"session-id": "session-123", "User-Agent": "hermes/test"},
            {"model": "m", "input": "Hello"},
            False,
        )
    finally:
        await client.close()

    assert "x-opencode-session" not in received_headers
    assert received_headers["User-Agent"] == "hermes/test"
