"""Payload rewriting — model forcing, tool injection, and per-model instructions."""

import logging
from typing import Any, Dict, Optional, Set, Tuple

import yaml  # type: ignore
from aiohttp import web  # type: ignore

from mcptap.http_utils import deep_getsizeof
from mcptap.mcp_intercept import MCPInterceptor
from mcptap.settings import (
    LOGGER,
    PROVIDER_OPENROUTER,
    PROVIDER_REQUESTY,
    settings,
)


def load_per_model_config() -> Dict[str, Dict[str, Any]]:
    """Load per-model configuration from MCP_TAP_PER_MODEL_YAML.

    Returns a dict mapping model identifiers to their config (e.g. instructions).
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
        if isinstance(cfg.get("instructions"), str):
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

    config = per_model_config.get(model)
    if config is None:
        if model:
            base = model.split(":")[0]
            config = per_model_config.get(base)
        else:
            return

    if config and "instructions" in config:
        payload["instructions"] = payload.get("instructions", "") + "\n\n" + config["instructions"]
        LOGGER.debug("Injected per-model instructions for model=%s", model)


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

    candidate_force_model = settings.model
    reasoning = payload.get("reasoning", {})
    if not isinstance(reasoning, dict):
        reasoning = {}
    reasoning_effort = reasoning.get("effort", None)
    if settings.plan_mode_trigger == reasoning_effort:
        candidate_force_model = settings.plan_mode_model

    if PROVIDER_REQUESTY == settings.upstream_provider:
        tools = [tool for tool in payload["tools"] if tool["type"] != "image_generation"]
        payload["tools"] = tools

        if "google" in candidate_force_model:
            tools = [tool for tool in payload["tools"] if tool["type"] in ("function", "namespace")]
            payload["tools"] = tools
            payload["tool_config"] = {"include_server_side_tool_invocations": True}

        del payload["include"]

    if PROVIDER_OPENROUTER == settings.upstream_provider:
        if candidate_force_model.startswith("@"):
            tools = [tool for tool in payload["tools"] if tool["type"] in ("function", "namespace")]
            payload["tools"] = tools

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
