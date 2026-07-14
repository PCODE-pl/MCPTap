"""Tests for the MCPTap tool-call hook gateway.

Covers:
- Session tracking (UUIDv7 timestamps, token accumulation)
- Hook execution (allow, block, errors)
- Synthetic get_goal flow (single and parallel calls)
- One-time pass-through after block (no recursion)
- JSON and SSE parity
- Missing hook = existing behavior
- Expired pending state cleanup
"""

import json
import os
import sys
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest  # type: ignore

# Ensure proxy module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_function_call_item(call_id: str, name: str, arguments: dict | None = None) -> dict:
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments or {}),
    }


def make_response(
    output_items: list,
    model: str = "test-model",
    total_tokens: int = 100,
    status: str = "completed",
) -> dict:
    return {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "status": status,
        "output": output_items,
        "usage": {"total_tokens": total_tokens} if total_tokens else None,
    }


def make_sse_bytes(response: dict) -> bytes:
    """Build SSE bytes that _response_json_from_sse can parse."""
    lines = []
    payload = {"type": "response.completed", "response": response}
    lines.append("event: response.completed")
    lines.append(f"data: {json.dumps(payload)}")
    lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def make_hook_script(action: str, message: str = "Blocked") -> str:
    """Create a temporary hook script file and return its path."""
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(path, "w") as f:
        f.write(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "data = json.load(sys.stdin)\n"
            f"print(json.dumps({{'action': '{action}', 'message': '{message}'}}))\n"
        )
    os.chmod(path, 0o755)
    return path


def make_error_hook_script(exit_code: int = 1) -> str:
    """Create a hook script that exits with a non-zero code."""
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(path, "w") as f:
        f.write(f"#!/usr/bin/env python3\nimport sys\nsys.exit({exit_code})\n")
    os.chmod(path, 0o755)
    return path


def make_bad_json_hook_script() -> str:
    """Create a hook script that returns invalid JSON."""
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(path, "w") as f:
        f.write("#!/usr/bin/env python3\nprint('not valid json')\n")
    os.chmod(path, 0o755)
    return path


# ---------------------------------------------------------------------------
# UUIDv7 timestamp tests
# ---------------------------------------------------------------------------


class TestUuidV7Timestamp:
    def test_valid_uuid_v7(self):
        # Create a UUIDv7: first 48 bits = unix timestamp in ms
        ts_ms = int(time.time() * 1000)
        # UUID v7 layout: 48-bit timestamp | 4-bit version(7) | 12-bit rand_a
        # | 2-bit variant(10) | 62-bit rand_b
        uuid_int = (ts_ms << 80) | (0x7 << 76) | (0x123 << 64) | (0x2 << 62) | 0x456789ABCDEF
        u = uuid.UUID(int=uuid_int)
        assert u.version == 7
        result = proxy._uuid_v7_timestamp(str(u))
        assert result is not None
        assert abs(result - ts_ms / 1000.0) < 0.001

    def test_invalid_uuid(self):
        assert proxy._uuid_v7_timestamp("not-a-uuid") is None

    def test_uuid_v4_not_v7(self):
        u = uuid.uuid4()
        assert proxy._uuid_v7_timestamp(str(u)) is None

    def test_empty_string(self):
        assert proxy._uuid_v7_timestamp("") is None


# ---------------------------------------------------------------------------
# SessionTracker tests
# ---------------------------------------------------------------------------


class TestSessionTracker:
    @pytest.mark.asyncio
    async def test_track_request_creates_session(self):
        tracker = proxy.SessionTracker()
        request = MagicMock()
        request.headers = {"session-id": "test-session-1"}

        info = await tracker.track_request(request, "model-1")
        assert info["session_id"] == "test-session-1"
        assert info["total_tokens"] == 0
        assert info["forced_model"] == "model-1"

    @pytest.mark.asyncio
    async def test_track_request_default_session(self):
        tracker = proxy.SessionTracker()
        request = MagicMock()
        request.headers = {}

        info = await tracker.track_request(request, "model-1")
        assert info["session_id"] == "default"

    @pytest.mark.asyncio
    async def test_add_usage_accumulates(self):
        tracker = proxy.SessionTracker()
        await tracker.add_usage("s1", 100)
        await tracker.add_usage("s1", 200)
        assert await tracker.get_usage("s1") == 300

    @pytest.mark.asyncio
    async def test_add_usage_creates_session_if_missing(self):
        tracker = proxy.SessionTracker()
        await tracker.add_usage("new-session", 50)
        assert await tracker.get_usage("new-session") == 50

    @pytest.mark.asyncio
    async def test_get_usage_nonexistent(self):
        tracker = proxy.SessionTracker()
        assert await tracker.get_usage("nonexistent") == 0

    @pytest.mark.asyncio
    async def test_get_elapsed_seconds(self):
        tracker = proxy.SessionTracker()
        request = MagicMock()
        request.headers = {"session-id": "s1"}
        await tracker.track_request(request, "model-1")
        elapsed = await tracker.get_elapsed_seconds("s1")
        assert elapsed >= 0.0
        assert elapsed < 10.0

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self):
        tracker = proxy.SessionTracker()
        await tracker.add_usage("s1", 100)
        await tracker.add_usage("s2", 200)
        assert await tracker.get_usage("s1") == 100
        assert await tracker.get_usage("s2") == 200

    @pytest.mark.asyncio
    async def test_uuid_v7_session_start(self):
        """If session-id is a UUIDv7, the embedded timestamp is used as start."""
        tracker = proxy.SessionTracker()
        ts_ms = int(time.time() * 1000) - 5000  # 5 seconds ago
        uuid_int = (ts_ms << 80) | (0x7 << 76) | (0x123 << 64) | (0x2 << 62) | 0x456789ABCDEF
        u = uuid.UUID(int=uuid_int)
        assert u.version == 7
        request = MagicMock()
        request.headers = {"session-id": str(u)}

        info = await tracker.track_request(request, "model-1")
        elapsed = await tracker.get_elapsed_seconds(info["session_id"])
        assert elapsed >= 5.0

    @pytest.mark.asyncio
    async def test_cleanup_expired(self):
        tracker = proxy.SessionTracker()
        # Manually set an old session
        tracker._sessions["old"] = {
            "session_id": "old",
            "start_time": time.time() - 7200,
            "total_tokens": 0,
            "forced_model": "m",
        }
        await tracker.cleanup_expired()
        assert "old" not in tracker._sessions


# ---------------------------------------------------------------------------
# PendingState tests
# ---------------------------------------------------------------------------


class TestPendingState:
    def test_is_expired_false_when_recent(self):
        state = proxy.PendingState(
            session_id="s1",
            saved_status=200,
            saved_headers={},
            saved_raw=b"",
            saved_body_json={},
            client_tool_calls=[],
            get_goal_result={},
            forced_model="m",
            used_tokens=0,
            used_time_seconds=0.0,
        )
        assert not state.is_expired()

    def test_is_expired_true_when_old(self):
        state = proxy.PendingState(
            session_id="s1",
            saved_status=200,
            saved_headers={},
            saved_raw=b"",
            saved_body_json={},
            client_tool_calls=[],
            get_goal_result={},
            forced_model="m",
            used_tokens=0,
            used_time_seconds=0.0,
        )
        state.created_at = time.time() - 9999
        assert state.is_expired()


# ---------------------------------------------------------------------------
# ToolHookGateway tests
# ---------------------------------------------------------------------------


class TestToolHookGateway:
    @pytest.mark.asyncio
    async def test_set_and_get_pending(self):
        tracker = proxy.SessionTracker()
        gw = proxy.ToolHookGateway(tracker)
        state = proxy.PendingState(
            session_id="s1",
            saved_status=200,
            saved_headers={},
            saved_raw=b"{}",
            saved_body_json={},
            client_tool_calls=[],
            get_goal_result={},
            forced_model="m",
            used_tokens=0,
            used_time_seconds=0.0,
        )
        await gw.set_pending("s1", state)
        retrieved = await gw.get_pending("s1")
        assert retrieved is not None
        assert retrieved.session_id == "s1"

    @pytest.mark.asyncio
    async def test_clear_pending(self):
        tracker = proxy.SessionTracker()
        gw = proxy.ToolHookGateway(tracker)
        state = proxy.PendingState(
            session_id="s1",
            saved_status=200,
            saved_headers={},
            saved_raw=b"",
            saved_body_json={},
            client_tool_calls=[],
            get_goal_result={},
            forced_model="m",
            used_tokens=0,
            used_time_seconds=0.0,
        )
        await gw.set_pending("s1", state)
        await gw.clear_pending("s1")
        assert await gw.get_pending("s1") is None

    @pytest.mark.asyncio
    async def test_get_pending_expired(self):
        tracker = proxy.SessionTracker()
        gw = proxy.ToolHookGateway(tracker)
        state = proxy.PendingState(
            session_id="s1",
            saved_status=200,
            saved_headers={},
            saved_raw=b"",
            saved_body_json={},
            client_tool_calls=[],
            get_goal_result={},
            forced_model="m",
            used_tokens=0,
            used_time_seconds=0.0,
        )
        state.created_at = time.time() - 9999
        await gw.set_pending("s1", state)
        assert await gw.get_pending("s1") is None

    @pytest.mark.asyncio
    async def test_run_hook_allow(self):
        hook_path = make_hook_script("allow")
        try:
            with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", hook_path):
                with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK_TIMEOUT", 10.0):
                    tracker = proxy.SessionTracker()
                    gw = proxy.ToolHookGateway(tracker)
                    state = proxy.PendingState(
                        session_id="s1",
                        saved_status=200,
                        saved_headers={},
                        saved_raw=b"",
                        saved_body_json={},
                        client_tool_calls=[{"call_id": "c1", "name": "shell", "arguments": {}}],
                        get_goal_result={},
                        forced_model="m",
                        used_tokens=500,
                        used_time_seconds=30.0,
                    )
                    result = await gw.run_hook(state)
                    assert result["action"] == "allow"
        finally:
            os.unlink(hook_path)

    @pytest.mark.asyncio
    async def test_run_hook_block(self):
        hook_path = make_hook_script("block", "Use consult_council")
        try:
            with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", hook_path):
                with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK_TIMEOUT", 10.0):
                    tracker = proxy.SessionTracker()
                    gw = proxy.ToolHookGateway(tracker)
                    state = proxy.PendingState(
                        session_id="s1",
                        saved_status=200,
                        saved_headers={},
                        saved_raw=b"",
                        saved_body_json={},
                        client_tool_calls=[{"call_id": "c1", "name": "shell", "arguments": {}}],
                        get_goal_result={},
                        forced_model="m",
                        used_tokens=15000,
                        used_time_seconds=200.0,
                    )
                    result = await gw.run_hook(state)
                    assert result["action"] == "block"
                    assert "consult_council" in result["message"]
        finally:
            os.unlink(hook_path)

    @pytest.mark.asyncio
    async def test_run_hook_nonzero_exit(self):
        hook_path = make_error_hook_script(exit_code=1)
        try:
            with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", hook_path):
                with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK_TIMEOUT", 10.0):
                    tracker = proxy.SessionTracker()
                    gw = proxy.ToolHookGateway(tracker)
                    state = proxy.PendingState(
                        session_id="s1",
                        saved_status=200,
                        saved_headers={},
                        saved_raw=b"",
                        saved_body_json={},
                        client_tool_calls=[],
                        get_goal_result={},
                        forced_model="m",
                        used_tokens=0,
                        used_time_seconds=0.0,
                    )
                    with pytest.raises(RuntimeError, match="exited with code"):
                        await gw.run_hook(state)
        finally:
            os.unlink(hook_path)

    @pytest.mark.asyncio
    async def test_run_hook_invalid_json(self):
        hook_path = make_bad_json_hook_script()
        try:
            with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", hook_path):
                with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK_TIMEOUT", 10.0):
                    tracker = proxy.SessionTracker()
                    gw = proxy.ToolHookGateway(tracker)
                    state = proxy.PendingState(
                        session_id="s1",
                        saved_status=200,
                        saved_headers={},
                        saved_raw=b"",
                        saved_body_json={},
                        client_tool_calls=[],
                        get_goal_result={},
                        forced_model="m",
                        used_tokens=0,
                        used_time_seconds=0.0,
                    )
                    with pytest.raises(RuntimeError, match="invalid JSON"):
                        await gw.run_hook(state)
        finally:
            os.unlink(hook_path)


# ---------------------------------------------------------------------------
# Extraction helper tests
# ---------------------------------------------------------------------------


class TestExtractionHelpers:
    def test_extract_usage_total_tokens(self):
        assert proxy._extract_usage_total_tokens({"usage": {"total_tokens": 500}}) == 500
        assert proxy._extract_usage_total_tokens({"usage": {}}) == 0
        assert proxy._extract_usage_total_tokens({}) == 0
        assert proxy._extract_usage_total_tokens(None) == 0

    def test_extract_client_tool_calls(self):
        body = {
            "output": [
                make_function_call_item("call1", "shell", {"cmd": "ls"}),
                make_function_call_item("call2", "consult_council", {"query": "test"}),
            ]
        }
        calls = proxy._extract_client_tool_calls(body, {"consult_council"})
        assert len(calls) == 1
        assert calls[0]["call_id"] == "call1"
        assert calls[0]["name"] == "shell"
        assert calls[0]["arguments"] == {"cmd": "ls"}

    def test_extract_client_tool_calls_empty(self):
        body = {"output": []}
        assert proxy._extract_client_tool_calls(body, set()) == []

    def test_has_intercepted_calls(self):
        body = {"output": [make_function_call_item("c1", "consult_council")]}
        assert proxy._has_intercepted_calls(body, {"consult_council"}) is True
        assert proxy._has_intercepted_calls(body, set()) is False

    def test_has_client_tool_calls(self):
        body = {"output": [make_function_call_item("c1", "shell")]}
        assert proxy._has_client_tool_calls(body, {"consult_council"}) is True
        assert proxy._has_client_tool_calls(body, {"shell"}) is False

    def test_strip_synthetic_get_goal(self):
        items = [
            {"type": "function_call", "call_id": proxy.SYNTHETIC_GET_GOAL_CALL_ID, "name": "get_goal"},
            {"type": "function_call_output", "call_id": proxy.SYNTHETIC_GET_GOAL_CALL_ID, "output": "{}"},
            {"type": "function_call", "call_id": "real_call", "name": "shell"},
        ]
        result = proxy._strip_synthetic_get_goal(items)
        assert len(result) == 1
        assert result[0]["call_id"] == "real_call"

    def test_extract_get_goal_result_dict(self):
        payload = {
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": proxy.SYNTHETIC_GET_GOAL_CALL_ID,
                    "output": {"status": "active", "tokensUsed": 500},
                },
            ]
        }
        result = proxy._extract_get_goal_result(payload)
        assert result["status"] == "active"
        assert result["tokensUsed"] == 500

    def test_extract_get_goal_result_json_string(self):
        payload = {
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": proxy.SYNTHETIC_GET_GOAL_CALL_ID,
                    "output": '{"status": "active"}',
                },
            ]
        }
        result = proxy._extract_get_goal_result(payload)
        assert result["status"] == "active"

    def test_extract_get_goal_result_not_found(self):
        payload = {"input": []}
        assert proxy._extract_get_goal_result(payload) == {}


# ---------------------------------------------------------------------------
# Synthetic response builder tests
# ---------------------------------------------------------------------------


class TestSyntheticResponse:
    def test_build_synthetic_get_goal_response(self):
        resp = proxy._build_synthetic_get_goal_response("test-model")
        assert resp["model"] == "test-model"
        assert resp["status"] == "incompleted"
        assert len(resp["output"]) == 1
        item = resp["output"][0]
        assert item["type"] == "function_call"
        assert item["call_id"] == proxy.SYNTHETIC_GET_GOAL_CALL_ID
        assert item["name"] == proxy.SYNTHETIC_GET_GOAL_TOOL_NAME

    def test_build_hook_error_response(self):
        resp = proxy._build_hook_error_response("test error", "model-1")
        assert resp["status"] == "failed"
        assert resp["error"]["type"] == "use_tool_hook_error"
        assert resp["error"]["message"] == "test error"

    def test_build_sse_from_response(self):
        resp = make_response([])
        sse_bytes = proxy._build_sse_from_response(resp)
        assert b"response.created" in sse_bytes
        assert b"response.completed" in sse_bytes
        assert b"[DONE]" in sse_bytes
        # Verify it can be parsed back
        parsed = proxy._response_json_from_sse(sse_bytes)
        assert parsed is not None
        assert parsed["id"] == resp["id"]


# ---------------------------------------------------------------------------
# Integration: end-to-end flow with mock upstream
# ---------------------------------------------------------------------------


class TestEndToEndFlow:
    """Test the full intercept loop with hook gateway using mocked upstream."""

    @pytest.mark.asyncio
    async def test_single_client_call_triggers_get_goal_then_allow(self):
        """One client tool call -> synthetic get_goal -> hook allow -> saved response returned."""
        hook_path = make_hook_script("allow")
        try:
            with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", hook_path):
                with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK_TIMEOUT", 10.0):
                    tracker = proxy.SessionTracker()
                    gw = proxy.ToolHookGateway(tracker)

                    # Simulate upstream response with a client tool call
                    saved_resp = make_response(
                        [
                            make_function_call_item("real_call_1", "shell", {"cmd": "ls"}),
                        ],
                        total_tokens=500,
                    )

                    # First call: should detect client calls and save state
                    state = proxy.PendingState(
                        session_id="s1",
                        saved_status=200,
                        saved_headers={"Content-Type": "application/json"},
                        saved_raw=json.dumps(saved_resp).encode("utf-8"),
                        saved_body_json=saved_resp,
                        client_tool_calls=[{"call_id": "real_call_1", "name": "shell", "arguments": {"cmd": "ls"}}],
                        get_goal_result={},
                        forced_model="test-model",
                        used_tokens=500,
                        used_time_seconds=10.0,
                    )
                    await gw.set_pending("s1", state)

                    # Verify pending state exists
                    pending = await gw.get_pending("s1")
                    assert pending is not None
                    assert len(pending.client_tool_calls) == 1

                    # Simulate get_goal result coming back
                    pending.get_goal_result = {"status": "active", "tokensUsed": 500}

                    # Run hook
                    decision = await gw.run_hook(pending)
                    assert decision["action"] == "allow"

                    # After allow, pending should be cleared
                    await gw.clear_pending("s1")
                    assert await gw.get_pending("s1") is None
        finally:
            os.unlink(hook_path)

    @pytest.mark.asyncio
    async def test_parallel_client_calls_single_hook(self):
        """Multiple parallel client tool calls -> one get_goal -> one hook run."""
        hook_path = make_hook_script("allow")
        try:
            with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", hook_path):
                with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK_TIMEOUT", 10.0):
                    tracker = proxy.SessionTracker()
                    gw = proxy.ToolHookGateway(tracker)

                    saved_resp = make_response(
                        [
                            make_function_call_item("call_1", "shell", {"cmd": "ls"}),
                            make_function_call_item("call_2", "shell", {"cmd": "pwd"}),
                            make_function_call_item("call_3", "shell", {"cmd": "whoami"}),
                        ],
                        total_tokens=800,
                    )

                    client_calls = proxy._extract_client_tool_calls(saved_resp, set())
                    assert len(client_calls) == 3

                    state = proxy.PendingState(
                        session_id="s1",
                        saved_status=200,
                        saved_headers={},
                        saved_raw=b"",
                        saved_body_json=saved_resp,
                        client_tool_calls=client_calls,
                        get_goal_result={},
                        forced_model="test-model",
                        used_tokens=800,
                        used_time_seconds=20.0,
                    )
                    await gw.set_pending("s1", state)

                    pending = await gw.get_pending("s1")
                    assert pending is not None
                    assert len(pending.client_tool_calls) == 3

                    # Run hook - should be called once for the batch
                    decision = await gw.run_hook(pending)
                    assert decision["action"] == "allow"

                    await gw.clear_pending("s1")
        finally:
            os.unlink(hook_path)

    @pytest.mark.asyncio
    async def test_block_then_pass_through_no_recursion(self):
        """After block, the next model response passes through without hook."""
        hook_path = make_hook_script("block", "Use consult_council")
        try:
            with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", hook_path):
                with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK_TIMEOUT", 10.0):
                    tracker = proxy.SessionTracker()
                    gw = proxy.ToolHookGateway(tracker)

                    saved_resp = make_response(
                        [
                            make_function_call_item("real_call_1", "shell"),
                        ],
                        total_tokens=500,
                    )

                    state = proxy.PendingState(
                        session_id="s1",
                        saved_status=200,
                        saved_headers={},
                        saved_raw=json.dumps(saved_resp).encode("utf-8"),
                        saved_body_json=saved_resp,
                        client_tool_calls=[{"call_id": "real_call_1", "name": "shell", "arguments": {}}],
                        get_goal_result={"status": "active"},
                        forced_model="test-model",
                        used_tokens=500,
                        used_time_seconds=10.0,
                    )

                    decision = await gw.run_hook(state)
                    assert decision["action"] == "block"

                    # After block, pending is cleared
                    await gw.clear_pending("s1")
                    # Next response should not trigger hook (pending is None)
                    assert await gw.get_pending("s1") is None
        finally:
            os.unlink(hook_path)

    @pytest.mark.asyncio
    async def test_consult_council_excluded_from_hook(self):
        """Intercepted MCP calls like consult_council are not subject to the hook."""
        intercept_names = {"consult_council", "council_health_check"}
        body = make_response(
            [
                make_function_call_item("c1", "consult_council", {"query": "test"}),
            ]
        )

        # consult_council is in intercept_names, so it's not a client call
        assert proxy._has_client_tool_calls(body, intercept_names) is False
        assert proxy._has_intercepted_calls(body, intercept_names) is True

        client_calls = proxy._extract_client_tool_calls(body, intercept_names)
        assert len(client_calls) == 0

    @pytest.mark.asyncio
    async def test_mixed_calls_intercepted_resolved_first(self):
        """Mixed calls: intercepted resolved first, client deferred to hook."""
        intercept_names = {"consult_council"}
        body = make_response(
            [
                make_function_call_item("c1", "consult_council", {"query": "review"}),
                make_function_call_item("c2", "shell", {"cmd": "ls"}),
            ]
        )

        assert proxy._has_intercepted_calls(body, intercept_names) is True
        assert proxy._has_client_tool_calls(body, intercept_names) is True

        # Intercepted calls are extracted for local execution
        intercept_calls = proxy._extract_intercepted_calls(
            body,
            type("MockIntercept", (), {"tool_names": lambda self: intercept_names})(),
        )
        assert len(intercept_calls) == 1
        assert intercept_calls[0][2] == "consult_council"

        # Client calls extracted for hook
        client_calls = proxy._extract_client_tool_calls(body, intercept_names)
        assert len(client_calls) == 1
        assert client_calls[0]["name"] == "shell"

    @pytest.mark.asyncio
    async def test_token_accumulation_multiple_responses(self):
        """Token usage accumulates across multiple upstream responses."""
        tracker = proxy.SessionTracker()

        resp1 = make_response([], total_tokens=100)
        resp2 = make_response([], total_tokens=200)
        resp3 = make_response([], total_tokens=300)

        await tracker.add_usage("s1", proxy._extract_usage_total_tokens(resp1))
        await tracker.add_usage("s1", proxy._extract_usage_total_tokens(resp2))
        await tracker.add_usage("s1", proxy._extract_usage_total_tokens(resp3))

        assert await tracker.get_usage("s1") == 600

    @pytest.mark.asyncio
    async def test_token_accumulation_multiple_sessions(self):
        """Token usage is independent per session."""
        tracker = proxy.SessionTracker()

        await tracker.add_usage("s1", 100)
        await tracker.add_usage("s2", 200)
        await tracker.add_usage("s1", 50)

        assert await tracker.get_usage("s1") == 150
        assert await tracker.get_usage("s2") == 200

    @pytest.mark.asyncio
    async def test_json_and_sse_parity(self):
        """Same response produces same parsed body from JSON and SSE."""
        resp = make_response(
            [
                make_function_call_item("c1", "shell", {"cmd": "ls"}),
            ],
            total_tokens=500,
        )

        # JSON path
        json_parsed = json.loads(json.dumps(resp).encode("utf-8").decode("utf-8"))

        # SSE path
        sse_bytes = make_sse_bytes(resp)
        sse_parsed = proxy._response_json_from_sse(sse_bytes)

        assert json_parsed["output"] == sse_parsed["output"]
        assert json_parsed["usage"] == sse_parsed["usage"]

        # Both have the same client tool calls
        json_calls = proxy._extract_client_tool_calls(json_parsed, set())
        sse_calls = proxy._extract_client_tool_calls(sse_parsed, set())
        assert len(json_calls) == 1
        assert len(sse_calls) == 1
        assert json_calls[0]["call_id"] == sse_calls[0]["call_id"]
        assert json_calls[0]["name"] == sse_calls[0]["name"]

    @pytest.mark.asyncio
    async def test_hook_error_blocks_tool_calls(self):
        """Non-zero exit code from hook blocks tool calls with error."""
        hook_path = make_error_hook_script(exit_code=1)
        try:
            with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", hook_path):
                with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK_TIMEOUT", 10.0):
                    tracker = proxy.SessionTracker()
                    gw = proxy.ToolHookGateway(tracker)
                    state = proxy.PendingState(
                        session_id="s1",
                        saved_status=200,
                        saved_headers={},
                        saved_raw=b"",
                        saved_body_json={},
                        client_tool_calls=[],
                        get_goal_result={},
                        forced_model="m",
                        used_tokens=0,
                        used_time_seconds=0.0,
                    )
                    with pytest.raises(RuntimeError):
                        await gw.run_hook(state)
        finally:
            os.unlink(hook_path)

    @pytest.mark.asyncio
    async def test_no_hook_preserves_existing_behavior(self):
        """When MCP_TAP_USE_TOOL_HOOK is empty, hook gateway is disabled."""
        with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", ""):
            tracker = proxy.SessionTracker()
            gw = proxy.ToolHookGateway(tracker)
            assert gw.enabled is False

            # No pending state should be created
            body = make_response(
                [
                    make_function_call_item("c1", "shell"),
                ]
            )
            intercept_names = set()
            has_client = proxy._has_client_tool_calls(body, intercept_names)
            assert has_client is True

            # Gateway disabled, so no pending state
            assert await gw.get_pending("s1") is None

    @pytest.mark.asyncio
    async def test_expired_pending_cleanup(self):
        """Expired pending states are cleaned up on get and set."""
        tracker = proxy.SessionTracker()
        gw = proxy.ToolHookGateway(tracker)

        state = proxy.PendingState(
            session_id="s1",
            saved_status=200,
            saved_headers={},
            saved_raw=b"",
            saved_body_json={},
            client_tool_calls=[],
            get_goal_result={},
            forced_model="m",
            used_tokens=0,
            used_time_seconds=0.0,
        )
        state.created_at = time.time() - 9999
        await gw.set_pending("s1", state)

        # get_pending should return None (expired)
        assert await gw.get_pending("s1") is None

        # Set a new state; old expired ones should be cleaned
        state2 = proxy.PendingState(
            session_id="s2",
            saved_status=200,
            saved_headers={},
            saved_raw=b"",
            saved_body_json={},
            client_tool_calls=[],
            get_goal_result={},
            forced_model="m",
            used_tokens=0,
            used_time_seconds=0.0,
        )
        await gw.set_pending("s2", state2)
        assert await gw.get_pending("s2") is not None
