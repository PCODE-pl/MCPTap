"""Tests for Responses API to Chat Completions compatibility."""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from mcptap.chat_completions import (
    ChatConversationStore,
    chat_response_to_responses,
    chat_sse_to_responses,
    convert_chat_response,
    responses_request_to_chat,
)
from mcptap.responses import response_json_from_sse
from mcptap.upstream import post_upstream_buffered


def test_responses_request_maps_messages_tools_and_reasoning():
    payload = {
        "model": "provider/model",
        "instructions": "You are a coding assistant.",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "Inspect this code."}]},
            {
                "type": "function_call",
                "call_id": "call_123",
                "name": "shell",
                "arguments": '{"cmd":"pwd"}',
            },
            {"type": "function_call_output", "call_id": "call_123", "output": " /tmp "},
        ],
        "tools": [
            {
                "type": "function",
                "name": "shell",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        "reasoning": {"effort": "high"},
        "max_output_tokens": 512,
        "previous_response_id": "resp_old",
    }
    store = ChatConversationStore()
    store.store("resp_old", [{"role": "user", "content": "Earlier"}])

    result = responses_request_to_chat(payload, store)

    assert result["model"] == payload["model"]
    assert result["messages"] == [
        {"role": "user", "content": "Earlier"},
        {"role": "system", "content": "You are a coding assistant."},
        {"role": "user", "content": "Inspect this code."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "shell", "arguments": '{"cmd":"pwd"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_123", "content": " /tmp "},
    ]
    assert result["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert result["reasoning_effort"] == "high"
    assert result["max_tokens"] == 512
    assert "previous_response_id" not in result
    assert "input" not in result
    assert "instructions" not in result


def test_responses_developer_message_maps_to_supported_system_role():
    result = responses_request_to_chat({"input": [{"role": "developer", "content": "Use concise answers."}]})

    assert result["messages"] == [{"role": "system", "content": "Use concise answers."}]


def test_empty_assistant_messages_are_not_sent_to_chat_upstream():
    result = responses_request_to_chat(
        {
            "input": [
                {"role": "assistant", "content": None},
                {"type": "message", "role": "assistant", "content": []},
                {"role": "user", "content": "Continue."},
            ]
        }
    )

    assert result["messages"] == [{"role": "user", "content": "Continue."}]


def test_assistant_tool_calls_are_kept_when_content_is_empty():
    result = responses_request_to_chat(
        {
            "input": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "shell", "arguments": "{}"},
                        }
                    ],
                }
            ]
        }
    )

    assert result["messages"][0]["role"] == "assistant"
    assert result["messages"][0]["content"] is None
    assert result["messages"][0]["tool_calls"][0]["id"] == "call_1"


def test_stream_request_enables_usage_reporting():
    result = responses_request_to_chat({"model": "m", "stream": True, "input": "Hello"})

    assert result["stream"] is True
    assert result["stream_options"] == {"include_usage": True}


def test_truncate_preserves_system_and_recent():
    from mcptap.chat_completions import _truncate_chat_messages

    msgs = [{"role": "system", "content": "sys"}] + [{"role": "user", "content": "x" * 200000}] * 5
    truncated = _truncate_chat_messages(msgs, max_chars=300000)
    assert truncated[0]["role"] == "system"
    assert len(truncated) < len(msgs)


def test_truncate_oversized_single_message_is_clipped():
    payload = {
        "model": "m",
        "instructions": "sys",
        "input": [{"role": "user", "content": "A" * 600000}],
        "stream": True,
    }
    result = responses_request_to_chat(payload, stream=True)
    total = len(json.dumps(result["messages"]))
    assert total <= 600000


def test_empty_assistant_history_is_removed_before_follow_up():
    store = ChatConversationStore()
    store.store(
        "resp_1",
        [
            {"role": "user", "content": "Start."},
            {"role": "assistant", "content": None},
        ],
    )

    result = responses_request_to_chat(
        {"previous_response_id": "resp_1", "input": "Continue."},
        store,
    )

    assert result["messages"] == [
        {"role": "user", "content": "Start."},
        {"role": "user", "content": "Continue."},
    ]


def test_chat_text_response_maps_to_responses_message_and_usage():
    body = {
        "id": "chatcmpl_123",
        "model": "provider/model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Done."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    }

    result = chat_response_to_responses(body)

    assert result["object"] == "response"
    assert result["model"] == body["model"]
    assert result["status"] == "completed"
    assert result["output"][0]["type"] == "message"
    assert result["output"][0]["content"] == [{"type": "output_text", "text": "Done.", "annotations": []}]
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}


def test_chat_tool_response_maps_to_responses_function_call():
    body = {
        "id": "chatcmpl_123",
        "model": "provider/model",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "I will inspect it.",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {"name": "shell", "arguments": '{"cmd":"pwd"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    result = chat_response_to_responses(body)

    assert result["status"] == "incomplete"
    assert result["output"] == [
        {
            "type": "function_call",
            "id": "call_123",
            "call_id": "call_123",
            "name": "shell",
            "arguments": '{"cmd":"pwd"}',
            "reasoning_content": "I will inspect it.",
        }
    ]

    history = ChatConversationStore()
    history.store_response("resp_1", [{"role": "user", "content": "Inspect it."}], body)
    stored = history.get("resp_1")
    assert stored is not None
    assert stored[1]["reasoning_content"] == "I will inspect it."


def test_chat_sse_aggregates_text_fragmented_tool_calls_and_usage():
    chunks = [
        {"id": "chatcmpl_1", "model": "m", "choices": [{"index": 0, "delta": {"role": "assistant"}}]},
        {"id": "chatcmpl_1", "model": "m", "choices": [{"index": 0, "delta": {"content": "Hel"}}]},
        {
            "id": "chatcmpl_1",
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "sh"}}]
                    },
                }
            ],
        },
        {
            "id": "chatcmpl_1",
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": "lo",
                        "tool_calls": [{"index": 0, "function": {"name": "ell", "arguments": '{"c'}}],
                    },
                }
            ],
        },
        {
            "id": "chatcmpl_1",
            "model": "m",
            "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'md":"pwd"}'}}]}}],
        },
        {
            "id": "chatcmpl_1",
            "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        },
    ]
    raw = b"".join(f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks) + b"data: [DONE]\n\n"

    result = chat_sse_to_responses(raw)

    assert result["usage"]["total_tokens"] == 8
    assert result["output"][0]["type"] == "function_call"
    assert result["output"][0]["arguments"] == '{"cmd":"pwd"}'
    assert result["output"][0]["name"] == "shell"
    assert result["output"][0]["reasoning_content"] if "reasoning_content" in result["output"][0] else True

    responses_sse = result["sse"]
    parsed = response_json_from_sse(responses_sse)
    assert parsed["output"][0]["call_id"] == "call_1"


def test_chat_json_response_received_for_stream_is_converted_to_responses_sse():
    raw = json.dumps(
        {
            "id": "chatcmpl_1",
            "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
        }
    ).encode()

    result = chat_sse_to_responses(raw)

    assert result["object"] == "response"
    assert b"response.completed" in result["sse"]


def test_chat_stream_emits_text_delta_and_content_part_events():
    raw = b"\n".join(
        [
            b'data: {"id":"chatcmpl_1","model":"m","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}',
            b'data: {"id":"chatcmpl_1","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
            b"data: [DONE]",
            b"",
        ]
    )

    result = chat_sse_to_responses(raw)
    sse = result["sse"]

    assert b"response.content_part.added" in sse
    assert b"event: " not in sse
    assert b"response.output_text.delta" in sse
    assert b'"delta":"Hello"' in sse
    assert b"response.output_text.done" in sse
    assert b"response.content_part.done" in sse
    assert b'"logprobs":[]' in sse
    assert b'"phase":"commentary"' in sse


def test_convert_chat_stream_does_not_serialize_internal_sse_bytes():
    raw = b'data: {"id":"chatcmpl_1","model":"m","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\ndata: {"id":"chatcmpl_1","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    converted_raw, converted_body = convert_chat_response(raw, stream=True, response_id="resp_1")

    assert converted_body is not None
    assert converted_body["id"] == "resp_1"
    assert b"response.output_text.delta" in converted_raw


def test_store_keeps_latest_response_history():
    store = ChatConversationStore()
    store.store("resp_1", [{"role": "user", "content": "Hello"}])

    assert store.get("resp_1") == [{"role": "user", "content": "Hello"}]
    assert store.get("missing") is None


def test_follow_up_request_does_not_duplicate_stored_instructions():
    store = ChatConversationStore()
    store.store("resp_1", [{"role": "system", "content": "Be concise."}, {"role": "assistant", "content": "Hi"}])

    result = responses_request_to_chat(
        {"previous_response_id": "resp_1", "instructions": "Be concise.", "input": "Continue."},
        store,
    )

    assert result["messages"] == [
        {"role": "system", "content": "Be concise."},
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Continue."},
    ]


@pytest.mark.asyncio
async def test_buffered_upstream_uses_chat_endpoint_and_returns_responses_body(monkeypatch):
    received = {}

    async def handler(request):
        received["path"] = request.path
        received["body"] = await request.json()
        return web.json_response(
            {
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            }
        )

    server = TestServer(web.Application())
    server.app.router.add_post("/v1/chat/completions", handler)
    client = TestClient(server)
    await client.start_server()
    try:
        from mcptap.settings import settings

        monkeypatch.setattr(settings, "use_chat_completions", True)
        monkeypatch.setattr(settings, "upstream_base_url", str(server.make_url("/v1")))
        monkeypatch.setattr(settings, "api_key", "test-key")
        monkeypatch.setattr(settings, "model", "m")
        monkeypatch.setattr(settings, "plan_mode_model", "m")
        _status, _headers, raw, body = await post_upstream_buffered(
            client.session,
            "/responses",
            {},
            {"model": "m", "input": "Hello"},
            False,
        )
    finally:
        await client.close()

    assert received["path"] == "/v1/chat/completions"
    assert received["body"]["messages"] == [{"role": "user", "content": "Hello"}]
    assert json.loads(raw)["object"] == "response"
    assert body["output"][0]["content"][0]["text"] == "Hello"


@pytest.mark.asyncio
async def test_responses_endpoint_is_used_when_chat_adapter_is_disabled(monkeypatch):
    received_paths = []

    async def handler(request):
        received_paths.append(request.path)
        return web.json_response({"id": "resp_1", "model": "other/model", "output": []})

    server = TestServer(web.Application())
    server.app.router.add_post("/v1/responses", handler)
    client = TestClient(server)
    await client.start_server()
    try:
        from mcptap.settings import settings

        monkeypatch.setattr(settings, "use_chat_completions", False)
        monkeypatch.setattr(settings, "upstream_provider", "nano-gpt")
        monkeypatch.setattr(settings, "model", "other/model")
        monkeypatch.setattr(settings, "upstream_base_url", str(server.make_url("/v1")))
        await post_upstream_buffered(
            client.session,
            "/responses",
            {},
            {"model": "other/model", "input": "Hello"},
            False,
        )
    finally:
        await client.close()

    assert received_paths == ["/v1/responses"]


@pytest.mark.asyncio
async def test_chat_mode_replays_previous_response_as_chat_history(monkeypatch):
    received_bodies = []

    async def handler(request):
        received_bodies.append(await request.json())
        if len(received_bodies) == 1:
            return web.json_response(
                {
                    "id": "chatcmpl_tool",
                    "model": "m",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "shell", "arguments": '{"cmd":"pwd"}'},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            )
        return web.json_response(
            {
                "id": "chatcmpl_done",
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "It is /tmp."},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    server = TestServer(web.Application())
    server.app.router.add_post("/v1/chat/completions", handler)
    client = TestClient(server)
    await client.start_server()
    try:
        from mcptap.settings import settings

        monkeypatch.setattr(settings, "use_chat_completions", True)
        monkeypatch.setattr(settings, "upstream_base_url", str(server.make_url("/v1")))
        monkeypatch.setattr(settings, "api_key", "test-key")
        monkeypatch.setattr(settings, "model", "m")
        monkeypatch.setattr(settings, "plan_mode_model", "m")

        status, _headers, _raw, body = await post_upstream_buffered(
            client.session,
            "/responses",
            {},
            {"model": "m", "input": "Call shell."},
            False,
        )
        assert status == 200
        assert body is not None
        previous_response_id = body["id"]
        await post_upstream_buffered(
            client.session,
            "/responses",
            {},
            {
                "model": "m",
                "previous_response_id": previous_response_id,
                "input": [{"type": "function_call_output", "call_id": "call_1", "output": "/tmp"}],
            },
            False,
        )
    finally:
        await client.close()

    assert received_bodies[1]["messages"] == [
        {"role": "user", "content": "Call shell."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": '{"cmd":"pwd"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "/tmp"},
    ]


@pytest.mark.asyncio
async def test_chat_mode_replays_function_tool_call_before_chat_conversion(monkeypatch):
    received_bodies = []

    async def handler(request):
        received_bodies.append(await request.json())
        return web.json_response(
            {
                "id": "chatcmpl_done",
                "model": "provider/model",
                "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            }
        )

    server = TestServer(web.Application())
    server.app.router.add_post("/v1/chat/completions", handler)
    client = TestClient(server)
    await client.start_server()
    try:
        from mcptap.settings import settings

        monkeypatch.setattr(settings, "use_chat_completions", True)
        monkeypatch.setattr(settings, "upstream_base_url", str(server.make_url("/v1")))
        monkeypatch.setattr(settings, "model", "any/model")
        monkeypatch.setattr(settings, "plan_mode_model", "any/model")
        payload = {
            "model": "any/model",
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "apply_patch",
                    "arguments": '{"input": "patch"}',
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "Applied"},
            ],
        }
        await post_upstream_buffered(client.session, "/responses", {}, payload, False)
    finally:
        await client.close()

    assert received_bodies[0]["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "apply_patch", "arguments": '{"input": "patch"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "Applied"},
    ]
