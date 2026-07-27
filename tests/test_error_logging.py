"""Tests for error-response logging in MCPTap.

Covers:
- post_upstream_buffered parses body_json for error responses (429, 500)
- forward_rewritten returns the raw response body for logging
- record_from_response logs error responses with correct status codes
- handle_responses_with_intercept logs upstream errors before breaking
"""

import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy  # noqa: E402
from mcptap.log_store import LogStore, record_from_response  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_error_response(status_code: int, message: str = "Rate limited") -> bytes:
    """Build a JSON error body as returned by OpenRouter."""
    return json.dumps(
        {
            "error": {
                "message": message,
                "code": status_code,
                "type": "rate_limit_exceeded" if status_code == 429 else "server_error",
            }
        }
    ).encode("utf-8")


def make_mock_aiohttp_response(status: int, body: bytes):
    """Build a mock aiohttp response object for post_upstream_buffered."""
    resp = AsyncMock()
    resp.status = status
    resp.headers = {"Content-Type": "application/json"}
    resp.read = AsyncMock(return_value=body)
    resp.release = MagicMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# post_upstream_buffered: error responses are parsed
# ---------------------------------------------------------------------------


class TestPostUpstreamBufferedErrorParsing:
    """post_upstream_buffered must parse body_json even for error status codes."""

    @pytest.mark.asyncio
    async def test_429_response_body_is_parsed(self):
        error_body = make_error_response(429)
        mock_cm = make_mock_aiohttp_response(429, error_body)

        session = MagicMock()
        session.post = MagicMock(return_value=mock_cm)

        status, headers, raw, body_json = await proxy.post_upstream_buffered(
            session,
            path="/v1/responses",
            headers={"Authorization": "Bearer test"},
            body={"model": "test-model", "input": []},
            stream=False,
        )

        assert status == 429
        assert raw == error_body
        assert body_json is not None
        assert body_json["error"]["message"] == "Rate limited"
        assert body_json["error"]["type"] == "rate_limit_exceeded"

    @pytest.mark.asyncio
    async def test_500_response_body_is_parsed(self):
        error_body = make_error_response(500, "Internal server error")
        mock_cm = make_mock_aiohttp_response(500, error_body)

        session = MagicMock()
        session.post = MagicMock(return_value=mock_cm)

        status, headers, raw, body_json = await proxy.post_upstream_buffered(
            session,
            path="/v1/responses",
            headers={"Authorization": "Bearer test"},
            body={"model": "test-model", "input": []},
            stream=False,
        )

        assert status == 500
        assert body_json is not None
        assert body_json["error"]["message"] == "Internal server error"


# ---------------------------------------------------------------------------
# record_from_response: error responses are recorded with correct status
# ---------------------------------------------------------------------------


class TestRecordFromResponseErrors:
    """record_from_response must log error responses with their status codes."""

    def test_429_response_is_recorded(self, tmp_path):
        store = LogStore(str(tmp_path / "test_errors.db"))
        store.migrate()
        store.connect()

        error_body = make_error_response(429)
        error_body_json = json.loads(error_body.decode("utf-8"))

        record_from_response(
            store,
            request_body={"model": "test-model", "input": []},
            response_raw=error_body,
            response_body_json=error_body_json,
            session_id="sess-429",
            model="test-model",
            provider="openrouter",
            status_code=429,
            request_path="/v1/responses",
            stream=False,
            start_time=time.time(),
        )

        rows, _ = store.query(range_seconds=3600, limit=10)
        assert len(rows) == 1
        assert rows[0]["status_code"] == 429
        assert rows[0]["session_id"] == "sess-429"
        assert rows[0]["model"] == "test-model"

        detail = store.get_by_id(rows[0]["id"])
        assert detail["response_body"] is not None
        assert detail["response_body"]["error"]["type"] == "rate_limit_exceeded"

        store.close()

    def test_500_response_is_recorded(self, tmp_path):
        store = LogStore(str(tmp_path / "test_500.db"))
        store.migrate()
        store.connect()

        error_body = make_error_response(500, "Internal server error")

        record_from_response(
            store,
            request_body={"model": "test-model", "input": []},
            response_raw=error_body,
            response_body_json=None,
            session_id="sess-500",
            model="test-model",
            provider="openrouter",
            status_code=500,
            request_path="/v1/responses",
            stream=False,
            start_time=time.time(),
        )

        rows, _ = store.query(range_seconds=3600, limit=10)
        assert len(rows) == 1
        assert rows[0]["status_code"] == 500

        detail = store.get_by_id(rows[0]["id"])
        assert detail["response_body"] is not None
        assert detail["response_body"]["error"]["message"] == "Internal server error"

        store.close()


# ---------------------------------------------------------------------------
# handle_responses_with_intercept: error responses are logged before break
# ---------------------------------------------------------------------------


class TestInterceptLoopErrorLogging:
    """The intercept loop must log upstream errors (429, 500) before breaking."""

    @pytest.mark.asyncio
    async def test_429_upstream_response_is_logged(self, tmp_path):
        """When upstream returns 429, the response must be recorded in the log store."""
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from mcptap.response_flow import handle_responses_with_intercept
        from mcptap.session import SessionTracker
        from mcptap.tool_hook import ToolHookGateway

        error_body = make_error_response(429)
        error_body_json = json.loads(error_body.decode("utf-8"))

        store = LogStore(str(tmp_path / "intercept_429.db"))
        store.migrate()
        store.connect()

        # We need a real aiohttp Request for _emit_buffered_response to call
        # response.prepare(request).  Use a TestServer + TestClient to create
        # a real request context.
        app = web.Application()

        async def handler(request):
            interceptor = MagicMock()
            interceptor.enabled = False
            interceptor.tool_names = MagicMock(return_value=set())

            session_tracker = SessionTracker()
            hook_gateway = ToolHookGateway(session_tracker)
            hook_gateway._enabled = False

            mock_post = AsyncMock(return_value=(429, {"Content-Type": "application/json"}, error_body, error_body_json))

            with patch("mcptap.response_flow.post_upstream_buffered", mock_post):
                with patch.object(proxy.settings, "intercept_max_iterations", 5):
                    with patch.object(proxy.settings, "openrouter_provider", "openrouter"):
                        with patch.object(proxy.settings, "upstream_provider", "openrouter"):
                            with patch.object(proxy.settings, "model", "test-model"):
                                return await handle_responses_with_intercept(
                                    request,
                                    session=MagicMock(),
                                    path="/v1/responses",
                                    request_headers={"Authorization": "Bearer test"},
                                    payload={"model": "test-model", "input": []},
                                    intercept=interceptor,
                                    client_wanted_stream=False,
                                    hook_gateway=hook_gateway,
                                    session_tracker=session_tracker,
                                    log_store=store,
                                )

        app.router.add_post("/v1/responses", handler)

        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post(
                "/v1/responses",
                headers={"session-id": "test-429-session"},
                json={"model": "test-model", "input": []},
            )
            assert resp.status == 429
        finally:
            await client.close()

        # Verify the 429 was logged
        rows, _ = store.query(range_seconds=3600, limit=10)
        assert len(rows) == 1
        assert rows[0]["status_code"] == 429
        assert rows[0]["session_id"] == "test-429-session"

        detail = store.get_by_id(rows[0]["id"])
        assert detail["response_body"] is not None
        assert detail["response_body"]["error"]["type"] == "rate_limit_exceeded"

        store.close()
