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
