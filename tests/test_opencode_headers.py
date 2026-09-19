"""Tests for OpenCode-specific upstream request headers."""

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer

from mcptap.upstream import _OPENCODE_USER_AGENT, _canonical_opencode_session, passthrough, post_upstream_buffered


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
        monkeypatch.setattr(settings, "use_chat_completions", False)
        await post_upstream_buffered(
            client.session,
            "/responses",
            {"session-id": "session-123", "User-Agent": "hermes/test"},
            {"model": "m", "input": "Hello"},
            False,
        )
    finally:
        await client.close()

    # Gate requires the canonical session shape; the mapping is deterministic
    # per client session id.
    expected_session = _canonical_opencode_session("session-123")
    assert received_headers["x-opencode-session"] == expected_session
    assert expected_session.startswith("ses_") and len(expected_session) == 30
    assert received_headers["User-Agent"] == _OPENCODE_USER_AGENT


@pytest.mark.asyncio
async def test_opencode_headers_forward_the_complete_client_fingerprint(monkeypatch):
    received_headers = []

    async def handler(request):
        received_headers.append(dict(request.headers))
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
        monkeypatch.setattr(settings, "use_chat_completions", False)
        request_headers = {"session-id": "session-123", "User-Agent": "hermes/test"}
        for _ in range(2):
            await post_upstream_buffered(
                client.session,
                "/responses",
                request_headers,
                {"model": "m", "input": "Hello"},
                False,
            )
    finally:
        await client.close()

    assert len(received_headers) == 2
    expected_session = _canonical_opencode_session("session-123")
    for headers in received_headers:
        assert headers["x-opencode-client"] == "cli"
        assert headers["x-opencode-session"] == expected_session
        assert headers["x-opencode-sesion"] == expected_session.removeprefix("ses_")
        assert headers["x-opencode-request"].startswith("msg_")
        assert len(headers["x-opencode-request"]) == len("msg_") + 32
    assert received_headers[0]["x-opencode-request"] != received_headers[1]["x-opencode-request"]


@pytest.mark.asyncio
async def test_opencode_headers_fall_back_to_prompt_cache_key(monkeypatch):
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
        monkeypatch.setattr(settings, "use_chat_completions", False)
        await post_upstream_buffered(
            client.session,
            "/responses",
            {"User-Agent": "hermes/test"},
            {"model": "m", "prompt_cache_key": "pck_session_123", "input": "Hello"},
            False,
        )
    finally:
        await client.close()

    assert received_headers["x-opencode-session"] == _canonical_opencode_session("pck_session_123")
    assert received_headers["User-Agent"] == _OPENCODE_USER_AGENT


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
            b"{}",
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

    assert received_headers["x-opencode-client"] == "cli"
    assert received_headers["x-opencode-session"] == _canonical_opencode_session("session-123")
    assert received_headers["x-opencode-sesion"] == _canonical_opencode_session("session-123").removeprefix("ses_")
    assert received_headers["x-opencode-request"].startswith("msg_")
    assert len(received_headers["x-opencode-request"]) == len("msg_") + 32
    assert received_headers["User-Agent"] == _OPENCODE_USER_AGENT


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
        monkeypatch.setattr(settings, "use_chat_completions", False)
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


@pytest.mark.asyncio
async def test_opencode_buffered_non_stream_gains_gate_stream_and_tools(monkeypatch):
    """A buffered non-stream call upstream streams with the tool quartet and
    the final response.completed payload is materialized back as JSON."""
    received = {}

    async def handler(request):
        import json

        received["headers"] = dict(request.headers)
        received["payload"] = await request.json()
        sse = (
            "event: response.completed\n"
            + "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_1", "model": "m", "output": [{"type": "message", "content": "hi"}]},
                }
            )
            + "\n\n"
        )
        return web.Response(body=sse.encode(), content_type="text/event-stream")

    server = TestServer(web.Application())
    server.app.router.add_post("/v1/responses", handler)
    client = TestClient(server)
    await client.start_server()
    try:
        from mcptap.settings import settings

        monkeypatch.setattr(settings, "upstream_provider", "opencode")
        monkeypatch.setattr(settings, "upstream_base_url", str(server.make_url("/v1")))
        monkeypatch.setattr(settings, "api_key", "test-key")
        monkeypatch.setattr(settings, "use_chat_completions", False)
        status, response_headers, raw, body_json = await post_upstream_buffered(
            client.session,
            "/responses",
            {"session-id": "session-123", "User-Agent": "hermes/test"},
            {"model": "m", "input": "Hello", "tools": [{"type": "function", "name": "grep", "parameters": {}}]},
            False,
        )
    finally:
        await client.close()

    payload = received["payload"]
    # Gate stream + tools injected upstream; client-declared tool names kept.
    assert payload["stream"] is True
    names = [tool["name"] for tool in payload["tools"]]
    assert names == ["grep", "bash", "glob", "read"]
    # Final response.completed materialized as JSON for the non-stream client.
    assert status == 200
    assert body_json["output"] == [{"type": "message", "content": "hi"}]
    assert response_headers["Content-Type"].startswith("application/json")
    assert raw.startswith(b'{"id"')


@pytest.mark.asyncio
async def test_opencode_stream_client_request_is_forwarded_unchanged(monkeypatch):
    """A streaming client keeps its SSE response; gate tools are merged in."""
    received = {}

    async def handler(request):
        import json

        received["payload"] = await request.json()
        sse = (
            "event: response.completed\n"
            + "data: "
            + json.dumps({"type": "response.completed", "response": {"id": "resp_1", "output": []}})
            + "\n\n"
        )
        return web.Response(body=sse.encode(), content_type="text/event-stream")

    server = TestServer(web.Application())
    server.app.router.add_post("/v1/responses", handler)
    client = TestClient(server)
    await client.start_server()
    try:
        from mcptap.settings import settings

        monkeypatch.setattr(settings, "upstream_provider", "opencode")
        monkeypatch.setattr(settings, "upstream_base_url", str(server.make_url("/v1")))
        monkeypatch.setattr(settings, "api_key", "test-key")
        monkeypatch.setattr(settings, "use_chat_completions", False)
        status, response_headers, raw, _body_json = await post_upstream_buffered(
            client.session,
            "/responses",
            {"User-Agent": "hermes/test"},
            {"model": "m", "input": "Hello"},
            True,
        )
    finally:
        await client.close()

    payload = received["payload"]
    assert payload["stream"] is True
    assert [tool["name"] for tool in payload["tools"]] == ["bash", "glob", "grep", "read"]
    assert response_headers["Content-Type"].startswith("text/event-stream")


def test_canonical_session_shape_is_stable():
    a = _canonical_opencode_session("abc")
    b = _canonical_opencode_session("abc")
    other = _canonical_opencode_session("abcd")
    import re

    for value in (a, b, other):
        assert re.fullmatch(r"ses_[0-9a-f]{12}[0-9A-Za-z]{14}", value)
    assert a == b
    assert a != other


@pytest.mark.asyncio
async def test_opencode_session_header_always_present(monkeypatch):
    """Without a client session the gate header is minted canonically."""
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
        monkeypatch.setattr(settings, "use_chat_completions", False)
        await post_upstream_buffered(
            client.session,
            "/responses",
            {"User-Agent": "hermes/test"},
            {"model": "m", "input": "Hello"},
            False,
        )
    finally:
        await client.close()

    import re

    session = received_headers.get("x-opencode-session", "")
    assert re.fullmatch(r"ses_[0-9a-f]{12}[0-9A-Za-z]{14}", session)
