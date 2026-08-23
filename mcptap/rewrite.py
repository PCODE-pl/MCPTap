"""Payload rewriting — model forcing, tool injection, and per-model instructions."""

import logging
from typing import Any, Dict, Optional, Set, Tuple

import yaml  # type: ignore
from aiohttp import web  # type: ignore

from mcptap.http_utils import deep_getsizeof
from mcptap.mcp_intercept import MCPInterceptor
from mcptap.settings import (
    LOGGER,
    PROVIDER_META,
    PROVIDER_OPENROUTER,
    PROVIDER_REQUESTY,
    settings,
)


def load_per_model_config() -> Dict[str, Dict[str, Any]]:
    """Load per-model configuration from MCP_TAP_PER_MODEL_YAML.

    Returns a dict mapping model identifiers to their config (e.g. instructions
    or per-model tool compatibility options such as disabling custom tools).
    Supports model names with suffixes like ':floor' (suffix is ignored for matching).
    Also supports '@preset/name' and 'policy/name' entries.
    """
    if not settings.per_model_yaml:
        return {}

    payload = settings.per_model_yaml
    if payload.startswith("@"):
        path = payload[1:]
        with open(path, "r", encoding="utf-8") as fh:
            payload = fh.read()

    data = yaml.safe_load(payload)
    if not isinstance(data, dict):
        LOGGER.warning("MCP_TAP_PER_MODEL_YAML must be a YAML dict")
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for model_key, cfg in data.items():
        if not isinstance(cfg, dict):
            continue
        base_model = model_key.split(":")[0] if ":" in model_key else model_key
        if (
            isinstance(cfg.get("instructions"), str)
            or isinstance(cfg.get("disable_builtin_tools"), bool)
            or isinstance(cfg.get("disable_custom_tools"), bool)
        ):
            result[model_key] = cfg
            if base_model != model_key:
                result[base_model] = cfg
    return result


def _apply_model_and_provider(
    payload: Dict[str, Any],
) -> Tuple[Optional[str], str, Optional[str]]:
    """Force the configured model and apply provider-specific settings in-place.

    Returns (original_model, forced_model, reasoning_effort).
    """
    original_model = payload.get("model")
    payload["model"] = settings.model
    reasoning = payload.get("reasoning", {})
    if not isinstance(reasoning, dict):
        reasoning = {}
    reasoning_effort = reasoning.get("effort", None)
    if settings.plan_mode_trigger == reasoning_effort:
        input_size = deep_getsizeof(payload.get("input", None))
        if input_size > settings.plan_mode_max_input_size:
            raise RuntimeError(
                f"Input size ({input_size}) exceeds MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE "
                f"({settings.plan_mode_max_input_size})"
            )
        payload["model"] = settings.plan_mode_model

    if PROVIDER_OPENROUTER == settings.upstream_provider:
        payload.pop("models", None)

        provider = payload.get("provider")
        if not isinstance(provider, dict):
            provider = {}
        else:
            provider = dict(provider)

        for key in ("only", "ignore", "order", "sort", "allow_fallbacks"):
            provider.pop(key, None)

        if settings.openrouter_provider:
            provider["only"] = [settings.openrouter_provider]

        if settings.openrouter_disable_provider_fallbacks:
            provider["allow_fallbacks"] = False

        if provider:
            payload["provider"] = provider

    return original_model, payload["model"], reasoning_effort


def _inject_tools(payload: Dict[str, Any], intercept: MCPInterceptor) -> None:
    tools = payload.get("tools") or []
    if not isinstance(tools, list):
        tools = []
    reserved: Set[str] = intercept.tool_names()
    tools = [t for t in tools if not (isinstance(t, dict) and t.get("name") in reserved)]
    for tool in intercept.tools:
        tools.append(tool.to_tool_definition())
    payload["tools"] = tools


def _make_nullable_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    if "const" in schema:
        return {"anyOf": [schema, {"type": "null"}]}

    for key in ("anyOf", "oneOf"):
        alternatives = schema.get(key)
        if isinstance(alternatives, list):
            if not any(isinstance(item, dict) and item.get("type") == "null" for item in alternatives):
                schema[key] = [*alternatives, {"type": "null"}]
            return schema

    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        if schema_type != "null":
            schema["type"] = [schema_type, "null"]
    elif isinstance(schema_type, list) and "null" not in schema_type:
        schema["type"] = [*schema_type, "null"]
    elif schema_type is None and "enum" not in schema:
        return {"anyOf": [schema, {"type": "null"}]}

    enum = schema.get("enum")
    if isinstance(enum, list) and None not in enum:
        schema["enum"] = [*enum, None]
    return schema


def _transform_meta_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    transformed = schema.copy()
    properties = transformed.get("properties")
    if isinstance(properties, dict):
        required = transformed.get("required")
        required_names = set(required) if isinstance(required, list) else set()
        transformed_properties = {}
        for name, property_schema in properties.items():
            if not isinstance(property_schema, dict):
                transformed_properties[name] = property_schema
                continue
            nested_schema = _transform_meta_schema(property_schema)
            if name not in required_names:
                nested_schema = _make_nullable_schema(nested_schema)
            transformed_properties[name] = nested_schema
        transformed["properties"] = transformed_properties
        transformed["required"] = list(properties.keys())

    for key in ("items", "contains", "if", "then", "else", "not"):
        nested_schema = transformed.get(key)
        if isinstance(nested_schema, dict):
            transformed[key] = _transform_meta_schema(nested_schema)

    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        nested_schemas = transformed.get(key)
        if isinstance(nested_schemas, list):
            transformed[key] = [
                _transform_meta_schema(item) if isinstance(item, dict) else item for item in nested_schemas
            ]

    return transformed


_META_UNSUPPORTED_TOOL_TYPES = {
    "custom",
    "tool_search",
    "computer_use_preview",
    "image_generation",
    "code_interpreter",
}

_BUILTIN_RESPONSES_TOOL_TYPES = {
    "web_search",
    "web_search_preview",
    "file_search",
    "computer_use",
    "computer_use_preview",
    "code_interpreter",
    "image_generation",
    "tool_search",
}


def _get_per_model_config(model: str, per_model_config: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    config = per_model_config.get(model)
    if config is None and model:
        config = per_model_config.get(model.split(":")[0])
    return config


def _transform_meta_tool_schemas(payload: Dict[str, Any]) -> None:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return

    transformed_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            transformed_tools.append(tool)
            continue
        tool_type = tool.get("type")
        if tool_type in _META_UNSUPPORTED_TOOL_TYPES:
            LOGGER.warning("Dropping unsupported tool type for Meta: %r (name=%r)", tool_type, tool.get("name"))
            continue
        # Meta supports web_search_preview, not bare web_search
        if tool_type == "web_search":
            LOGGER.info("Rewriting web_search -> web_search_preview for Meta")
            tool = {**tool, "type": "web_search_preview"}
            tool_type = "web_search_preview"
        transformed_tool = tool.copy()
        if transformed_tool.get("type") == "web_search_preview":
            # Meta Responses executor only supports "text"
            sct = transformed_tool.get("search_content_types")
            if isinstance(sct, list):
                filtered = [v for v in sct if v == "text"]
                if filtered:
                    transformed_tool["search_content_types"] = filtered
                else:
                    transformed_tool.pop("search_content_types", None)
        else:
            transformed_tool.pop("search_content_types", None)
        parameters = transformed_tool.get("parameters")
        if isinstance(parameters, dict):
            transformed_tool["parameters"] = _transform_meta_schema(parameters)
        function = transformed_tool.get("function")
        if isinstance(function, dict):
            transformed_function = function.copy()
            function_parameters = transformed_function.get("parameters")
            if isinstance(function_parameters, dict):
                transformed_function["parameters"] = _transform_meta_schema(function_parameters)
            transformed_tool["function"] = transformed_function
        transformed_tools.append(transformed_tool)
    payload["tools"] = transformed_tools


def _inject_per_model_instructions(
    payload: Dict[str, Any],
    model: str,
    per_model_config: Dict[str, Dict[str, Any]],
) -> None:
    """Inject instructions from per-model config into the payload.

    Only injects on first request (no previous_response_id).
    Supports model names with suffixes (e.g., ':floor') - strips suffix for fallback match.
    """
    if payload.get("previous_response_id") is not None:
        return

    config = _get_per_model_config(model, per_model_config)
    instructions = config.get("instructions") if config else None
    if isinstance(instructions, str):
        payload["instructions"] = payload.get("instructions", "") + "\n\n" + instructions
        LOGGER.debug("Injected per-model instructions for model=%s", model)


def _disable_per_model_builtin_tools(
    payload: Dict[str, Any],
    model: str,
    per_model_config: Dict[str, Dict[str, Any]],
) -> None:
    """Remove Responses built-in tools for models that do not support them."""
    config = _get_per_model_config(model, per_model_config)
    if not config or config.get("disable_builtin_tools") is not True:
        return

    tools = payload.get("tools")
    if not isinstance(tools, list):
        return

    payload["tools"] = [
        tool for tool in tools if not isinstance(tool, dict) or tool.get("type") not in _BUILTIN_RESPONSES_TOOL_TYPES
    ]
    LOGGER.debug("Disabled Responses built-in tools for model=%s", model)


def _disable_per_model_custom_tools(
    payload: Dict[str, Any],
    model: str,
    per_model_config: Dict[str, Dict[str, Any]],
) -> None:
    """Remove custom tools for models that do not support them."""
    config = _get_per_model_config(model, per_model_config)
    if not config or config.get("disable_custom_tools") is not True:
        return

    tools = payload.get("tools")
    if not isinstance(tools, list):
        return

    payload["tools"] = [tool for tool in tools if not isinstance(tool, dict) or tool.get("type") != "custom"]
    LOGGER.debug("Disabled custom tools for model=%s", model)


def rewrite_json_payload(
    request: web.Request,
    payload: Dict[str, Any],
    intercept: MCPInterceptor,
    per_model_config: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[str], str, Optional[str]]:
    """In-place rewrite. Returns (original_model, forced_model, reasoning_effort)."""
    if LOGGER.isEnabledFor(logging.DEBUG):
        LOGGER.debug(
            "%s %s payload_keys=%r",
            request.method,
            request.path_qs,
            list(payload.keys()),
        )

    original_model, forced_model, reasoning_effort = _apply_model_and_provider(payload)
    _inject_tools(payload, intercept)
    if forced_model:
        _inject_per_model_instructions(payload, forced_model, per_model_config)
        _disable_per_model_builtin_tools(payload, forced_model, per_model_config)
        _disable_per_model_custom_tools(payload, forced_model, per_model_config)

    candidate_force_model = (
        settings.plan_mode_model if reasoning_effort == settings.plan_mode_trigger else settings.model
    )

    if PROVIDER_REQUESTY == settings.upstream_provider:
        tools = [tool for tool in payload["tools"] if tool["type"] != "image_generation"]
        payload["tools"] = tools

        if "google" in candidate_force_model:
            tools = [tool for tool in payload["tools"] if tool["type"] in ("function", "namespace")]
            payload["tools"] = tools
            payload["tool_config"] = {"include_server_side_tool_invocations": True}

        if "include" in payload:
            del payload["include"]

    if PROVIDER_OPENROUTER == settings.upstream_provider:
        if settings.model.startswith("@"):
            tools = [tool for tool in payload["tools"] if tool["type"] in ("function", "namespace")]
            payload["tools"] = tools

    if PROVIDER_META == settings.upstream_provider:
        _transform_meta_tool_schemas(payload)

    if LOGGER.isEnabledFor(logging.DEBUG):
        for key in ("tools",):
            LOGGER.debug(
                "%s %s After rewrite payload_key=%r payload_value=%r",
                request.method,
                request.path_qs,
                key,
                payload.get(key),
            )
    return original_model, forced_model, reasoning_effort
