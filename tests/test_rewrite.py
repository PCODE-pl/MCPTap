"""Tests for provider-specific request payload rewriting."""

import json
from unittest.mock import MagicMock, patch

from mcptap.mcp_intercept import MCPInterceptor
from mcptap.rewrite import (
    convert_muse_custom_input_items,
    convert_muse_function_response,
    load_per_model_config,
    rewrite_json_payload,
)
from mcptap.settings import PROVIDER_META, PROVIDER_OPENROUTER, settings


def test_per_model_disable_builtin_tools_keeps_function_tools_on_every_request():
    with (
        patch.object(
            settings,
            "per_model_yaml",
            "muse/spark:\n  disable_builtin_tools: true\n",
        ),
        patch.object(settings, "model", "muse/spark"),
        patch.object(settings, "upstream_provider", "requesty"),
    ):
        per_model_config = load_per_model_config()
        assert per_model_config["muse/spark"]["disable_builtin_tools"] is True

        for payload in (
            {
                "model": "client-model",
                "input": [],
                "tools": [
                    {"type": "web_search_preview"},
                    {"type": "file_search"},
                    {"type": "function", "name": "exec", "parameters": {"type": "object"}},
                ],
            },
            {
                "model": "client-model",
                "previous_response_id": "resp_123",
                "input": [],
                "tools": [{"type": "code_interpreter"}, {"type": "function", "name": "exec"}],
            },
        ):
            rewrite_json_payload(
                MagicMock(method="POST", path_qs="/v1/responses"), payload, MCPInterceptor(None), per_model_config
            )
            assert [tool["type"] for tool in payload["tools"]] == ["function"]


def test_muse_spark_converts_custom_tools_for_openrouter_on_every_request():
    with (
        patch.object(settings, "model", "meta/muse-spark-1.2"),
        patch.object(settings, "upstream_provider", PROVIDER_OPENROUTER),
    ):
        for payload in (
            {
                "model": "client-model",
                "input": [],
                "tools": [
                    {"type": "custom", "name": "apply_patch"},
                    {"type": "function", "name": "exec"},
                ],
            },
            {
                "model": "client-model",
                "previous_response_id": "resp_123",
                "input": [],
                "tools": [{"type": "custom", "name": "apply_patch"}, {"type": "function", "name": "exec"}],
            },
        ):
            rewrite_json_payload(MagicMock(method="POST", path_qs="/v1/responses"), payload, MCPInterceptor(None), {})
            assert [tool["type"] for tool in payload["tools"]] == ["function", "function"]
            assert payload["tools"][0]["name"] == "apply_patch"
            assert payload["tools"][0]["parameters"] == {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
                "additionalProperties": False,
            }


def test_muse_spark_converts_custom_tools_for_nano_gpt():
    with (
        patch.object(settings, "model", "meta/muse-spark-1.2-contributor"),
        patch.object(settings, "upstream_provider", "nano-gpt"),
    ):
        payload = {
            "model": "client-model",
            "input": [],
            "tools": [{"type": "custom", "name": "apply_patch"}, {"type": "function", "name": "exec"}],
        }

        rewrite_json_payload(MagicMock(method="POST", path_qs="/v1/responses"), payload, MCPInterceptor(None), {})

        assert [tool["type"] for tool in payload["tools"]] == ["function", "function"]
        assert payload["tools"][0]["name"] == "apply_patch"


def test_muse_spark_converts_all_custom_tools_for_nano_gpt():
    with (
        patch.object(settings, "model", "meta/muse-spark-1.2"),
        patch.object(settings, "upstream_provider", "nano-gpt"),
    ):
        payload = {
            "model": "client-model",
            "input": [],
            "tools": [
                {"type": "custom", "name": "apply_patch"},
                {"type": "custom", "name": "freeform_tool", "description": "Freeform input"},
            ],
        }

        rewrite_json_payload(MagicMock(method="POST", path_qs="/v1/responses"), payload, MCPInterceptor(None), {})

        assert [tool["type"] for tool in payload["tools"]] == ["function", "custom"]


def test_muse_spark_function_response_conversion_preserves_non_tool_output():
    body = {
        "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "OK"}]},
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "apply_patch",
                "arguments": json.dumps({"input": "patch"}),
            },
        ]
    }

    converted = convert_muse_function_response(body, "meta/muse-spark-1.2")

    assert converted["output"][0] == body["output"][0]
    assert converted["output"][1]["type"] == "custom_tool_call"


def test_custom_tools_are_preserved_for_other_models():
    with (
        patch.object(settings, "model", "openai/gpt-5.6-luna"),
        patch.object(settings, "upstream_provider", PROVIDER_OPENROUTER),
    ):
        payload = {
            "model": "client-model",
            "input": [],
            "tools": [{"type": "custom", "name": "custom_tool"}],
        }

        rewrite_json_payload(MagicMock(method="POST", path_qs="/v1/responses"), payload, MCPInterceptor(None), {})

        assert payload["tools"] == [{"type": "custom", "name": "custom_tool"}]


def test_muse_custom_input_is_converted_to_function_call():
    payload = {
        "model": "meta/muse-spark-1.2-contributor",
        "input": [
            {
                "type": "custom_tool_call",
                "call_id": "call_1",
                "name": "apply_patch",
                "input": "*** Begin Patch\n*** End Patch",
            },
            {
                "type": "custom_tool_call_output",
                "call_id": "call_1",
                "output": "Applied.",
            },
        ],
    }

    convert_muse_custom_input_items(payload, payload["model"])

    assert payload["input"] == [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "apply_patch",
            "arguments": json.dumps({"input": "*** Begin Patch\n*** End Patch"}),
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "Applied."},
    ]


def test_muse_function_response_is_converted_back_to_custom_tool_call():
    body = {
        "output": [
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "apply_patch",
                "arguments": json.dumps({"input": "*** Begin Patch\n*** End Patch"}),
            }
        ]
    }

    converted = convert_muse_function_response(body, "meta/muse-spark-1.2")

    assert converted["output"][0] == {
        "type": "custom_tool_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "apply_patch",
        "input": "*** Begin Patch\n*** End Patch",
    }
    assert body["output"][0]["type"] == "function_call"


def test_meta_tool_schemas_require_all_properties_and_nullable_optional_values():
    payload = {
        "model": "client-model",
        "input": [],
        "tools": [
            {
                "type": "function",
                "name": "search",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            }
        ],
    }

    with patch.object(settings, "upstream_provider", PROVIDER_META):
        rewrite_json_payload(MagicMock(method="POST", path_qs="/v1/responses"), payload, MCPInterceptor(None), {})

    schema = payload["tools"][0]["parameters"]
    assert schema["required"] == ["query", "limit"]
    assert schema["properties"]["query"] == {"type": "string"}
    assert schema["properties"]["limit"]["type"] == ["integer", "null"]


def test_meta_tool_schema_transformation_applies_to_nested_objects():
    payload = {
        "model": "client-model",
        "input": [],
        "tools": [
            {
                "type": "function",
                "name": "configure",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "options": {
                            "type": "object",
                            "properties": {"verbose": {"type": "boolean"}},
                        }
                    },
                },
            }
        ],
    }

    with patch.object(settings, "upstream_provider", PROVIDER_META):
        rewrite_json_payload(MagicMock(method="POST", path_qs="/v1/responses"), payload, MCPInterceptor(None), {})

    nested_schema = payload["tools"][0]["parameters"]["properties"]["options"]
    assert nested_schema["type"] == ["object", "null"]
    assert nested_schema["required"] == ["verbose"]
    assert nested_schema["properties"]["verbose"]["type"] == ["boolean", "null"]


def test_meta_tool_schema_transformation_drops_search_content_types_from_non_preview_tools():
    payload = {
        "model": "client-model",
        "input": [],
        "tools": [
            {
                "type": "function",
                "name": "search",
                "search_content_types": ["text"],
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "type": "web_search_preview",
                "search_content_types": ["text"],
            },
        ],
    }

    with patch.object(settings, "upstream_provider", PROVIDER_META):
        rewrite_json_payload(MagicMock(method="POST", path_qs="/v1/responses"), payload, MCPInterceptor(None), {})

    assert "search_content_types" not in payload["tools"][0]
    assert payload["tools"][1]["search_content_types"] == ["text"]


def test_meta_drops_unsupported_tool_types_and_rewrites_web_search():
    payload = {
        "model": "client-model",
        "input": [],
        "tools": [
            {"type": "custom", "name": "apply_patch", "description": "x", "format": {"type": "grammar"}},
            {
                "type": "tool_search",
                "execution": "client",
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "number"}, "query": {"type": "string"}},
                    "required": ["query"],
                },
            },
            {"type": "web_search", "search_content_types": ["text", "image"], "external_web_access": True},
            {
                "type": "function",
                "name": "exec",
                "parameters": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}, "workdir": {"type": "string"}},
                    "required": ["cmd"],
                },
            },
        ],
    }

    with patch.object(settings, "upstream_provider", PROVIDER_META):
        rewrite_json_payload(MagicMock(method="POST", path_qs="/v1/responses"), payload, MCPInterceptor(None), {})

    types = [t["type"] for t in payload["tools"]]
    assert "custom" not in types
    assert "tool_search" not in types
    assert "web_search" not in types
    assert "web_search_preview" in types
    # web_search_preview keeps only "text" for Meta (image is unsupported)
    assert payload["tools"][0]["search_content_types"] == ["text"]
    exec_tool = next(t for t in payload["tools"] if t.get("name") == "exec")
    assert exec_tool["parameters"]["required"] == ["cmd", "workdir"]
    assert exec_tool["parameters"]["properties"]["workdir"]["type"] == ["string", "null"]
