"""Function call/output pair repair in the buffered upstream path."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from mcptap.upstream import _repair_function_call_pairs, post_upstream_buffered


def test_dedupe_keeps_last_output_per_call_id():
    body = {
        "model": "m",
        "input": [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "call_id": "call_1", "name": "bash", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "stale"},
            {"type": "function_call_output", "call_id": "call_1", "output": "fresh"},
        ],
    }
    _repair_function_call_pairs(body)
    outputs = [i for i in body["input"] if i.get("type") == "function_call_output"]
    assert len(outputs) == 1
    assert outputs[0]["output"] == "fresh"
    # Non-output items keep their relative order.
    assert [i.get("type") for i in body["input"]] == ["message", "function_call", "function_call_output"]


def test_dedupe_handles_custom_tool_outputs_and_missing_call_id():
    body = {
        "model": "m",
        "input": [
            {"type": "custom_tool_call_output", "call_id": "c_1", "output": "a"},
            {"type": "custom_tool_call_output", "call_id": "c_1", "output": "b"},
            {"type": "function_call_output", "output": "no call id"},
            {"type": "message", "role": "user", "content": "x"},
        ],
    }
    _repair_function_call_pairs(body)
    assert len(body["input"]) == 3
    custom = [i for i in body["input"] if i.get("type") == "custom_tool_call_output"]
    assert len(custom) == 1 and custom[0]["output"] == "b"


def test_dedupe_noop_for_string_input_and_unique_outputs():
    body = {"model": "m", "input": "plain text"}
    _repair_function_call_pairs(body)
    assert body["input"] == "plain text"

    body2 = {
        "model": "m",
        "input": [
            {"type": "function_call_output", "call_id": "a", "output": "1"},
            {"type": "function_call_output", "call_id": "b", "output": "2"},
        ],
    }
    _repair_function_call_pairs(body2)
    assert len(body2["input"]) == 2


@pytest.mark.asyncio
async def test_duplicate_outputs_removed_before_upstream_send(monkeypatch):
    received = {}

    async def handler(request):
        received["payload"] = await request.json()
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
        status, _headers, _raw, _body = await post_upstream_buffered(
            client.session,
            "/responses",
            {},
            {
                "model": "m",
                "input": [
                    {"type": "function_call", "call_id": "call_1", "name": "bash", "arguments": "{}"},
                    {"type": "function_call_output", "call_id": "call_1", "output": "stale"},
                    {"type": "function_call_output", "call_id": "call_1", "output": "fresh"},
                ],
            },
            False,
        )
    finally:
        await client.close()

    assert status == 200
    sent_outputs = [i for i in received["payload"]["input"] if i.get("type") == "function_call_output"]
    assert len(sent_outputs) == 1
    assert sent_outputs[0]["output"] == "fresh"


def test_orphan_call_gets_synthetic_output():
    body = {
        "model": "m",
        "input": [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "call_id": "call_a", "name": "bash", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_a", "output": "ok"},
            {"type": "function_call", "call_id": "call_b", "name": "grep", "arguments": "{}"},
        ],
    }
    _repair_function_call_pairs(body)
    call_ids = [i.get("call_id") for i in body["input"] if i.get("type") == "function_call"]
    out_by_id = {i.get("call_id"): i.get("output") for i in body["input"] if i.get("type") == "function_call_output"}
    assert call_ids == ["call_a", "call_b"]
    assert out_by_id["call_a"] == "ok"
    assert out_by_id["call_b"] == "(tool result missing)"


def test_orphan_custom_tool_call_gets_matching_output_type():
    body = {
        "model": "m",
        "input": [
            {"type": "custom_tool_call", "call_id": "c_9", "name": "patch", "input": "x"},
        ],
    }
    _repair_function_call_pairs(body)
    outputs = [i for i in body["input"] if i.get("type") == "custom_tool_call_output"]
    assert len(outputs) == 1 and outputs[0]["call_id"] == "c_9"
