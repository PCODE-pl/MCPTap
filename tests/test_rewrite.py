"""Tests for provider-specific request payload rewriting."""

from unittest.mock import MagicMock, patch

from mcptap.mcp_intercept import MCPInterceptor
from mcptap.rewrite import rewrite_json_payload
from mcptap.settings import PROVIDER_META, settings


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
