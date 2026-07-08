#!/usr/bin/env python3
"""Transparent LLM proxy that forces a model.

Additionally supports transparent MCP tool interception: tools declared in
MCP_TAP_INTERCEPT_YAML are exposed to the model as regular function tools, and when
the model calls them the proxy executes the real MCP tool locally, feeds the
result back into the conversation, and only surfaces the final assistant
response to the client. The client never sees the intercepted tool calls.

Compatible with Python 3.10+;
It supports normal JSON responses and streaming SSE responses.

to see logs:
journalctl --user -u mcptap.service -f
for DEBUG level:
journalctl --user -u mcptap.service -p debug -f
"""

import asyncio
import contextlib
import copy
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml
from aiohttp import (  # type: ignore
    ClientError,
    ClientSession,
    ClientTimeout,
    TCPConnector,
    web,
)
from dotenv import load_dotenv  # type: ignore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".config/mcptap"
load_dotenv(CONFIG_DIR / "proxy.env", override=True)

MCP_TAP_LISTEN_HOST = os.environ.get("MCP_TAP_LISTEN_HOST", "127.0.0.1")
MCP_TAP_LISTEN_PORT = int(os.environ.get("MCP_TAP_LISTEN_PORT", "8787"))

MCP_TAP_OPENROUTER_PROVIDER = os.environ.get("MCP_TAP_OPENROUTER_PROVIDER", "").strip()
MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS = os.environ.get(
    "MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS", "1"
).lower() not in {
    "0",
    "false",
    "no",
    "off",
}
MCP_TAP_PLAN_MODE_TRIGGER = os.environ.get("MCP_TAP_PLAN_MODE_TRIGGER", "max").strip()
MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE = int(os.environ.get("MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE", 100000))
MCP_TAP_INTERCEPT_YAML = os.environ.get("MCP_TAP_INTERCEPT_YAML", "").strip()
MCP_TAP_INTERCEPT_MAX_ITERATIONS = int(os.environ.get("MCP_TAP_INTERCEPT_MAX_ITERATIONS", "8"))
MCP_TAP_INTERCEPT_TOOL_TIMEOUT = float(os.environ.get("MCP_TAP_INTERCEPT_TOOL_TIMEOUT", "120"))
MCP_TAP_LOG_LEVEL = os.environ.get("MCP_TAP_LOG_LEVEL", "INFO").upper()
MCP_TAP_LOG_FILE = os.environ.get("MCP_TAP_LOG_FILE", "").strip()
LOG_FILE_REDACT_HEADERS = os.environ.get("LOG_FILE_REDACT_HEADERS", "0").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
LOG_PAYLOAD_KEYS = [
    "tools",
]

PROVIDER_OPENROUTER = "openrouter"
PROVIDER_REQUESTY = "requesty"
MCP_TAP_UPSTREAM_PROVIDER = os.environ.get("MCP_TAP_UPSTREAM_PROVIDER")
UPSTREAM_BASE_URL = ""
PROVIDER_ENV_FILE = ""

if PROVIDER_OPENROUTER == MCP_TAP_UPSTREAM_PROVIDER:
    UPSTREAM_BASE_URL = "https://openrouter.ai/api/v1"
    PROVIDER_ENV_FILE = "openrouter.env"
elif PROVIDER_REQUESTY == MCP_TAP_UPSTREAM_PROVIDER:
    UPSTREAM_BASE_URL = "https://router.requesty.ai/v1"
    PROVIDER_ENV_FILE = "requesty.env"
if not UPSTREAM_BASE_URL:
    raise RuntimeError("MCP_TAP_UPSTREAM_PROVIDER must be one of 'openrouter' or 'requesty'")

load_dotenv(CONFIG_DIR / PROVIDER_ENV_FILE, override=True)

MCP_TAP_API_KEY = os.environ.get("MCP_TAP_API_KEY").strip()
if not MCP_TAP_API_KEY:
    raise RuntimeError("MCP_TAP_API_KEY must not be empty")

MCP_TAP_MODEL = os.environ.get("MCP_TAP_MODEL").strip()
MCP_TAP_PLAN_MODE_MODEL = os.environ.get("MCP_TAP_PLAN_MODE_MODEL").strip()

if not MCP_TAP_MODEL or not MCP_TAP_PLAN_MODE_MODEL:
    raise RuntimeError("MCP_TAP_MODEL and MCP_TAP_PLAN_MODE_MODEL must not be empty")

if PROVIDER_REQUESTY == MCP_TAP_UPSTREAM_PROVIDER:
    # requesty.ai models require '-responses' in model's name vendor
    if MCP_TAP_MODEL.startswith("openai") and "-responses" not in MCP_TAP_MODEL:
        vendor, model = MCP_TAP_MODEL.split("/", 1)
        MCP_TAP_MODEL = f"{vendor}-responses/{model}"
    if MCP_TAP_PLAN_MODE_MODEL.startswith("openai") and "-responses" not in MCP_TAP_PLAN_MODE_MODEL:
        vendor, model = MCP_TAP_PLAN_MODE_MODEL.split("/", 1)
        MCP_TAP_PLAN_MODE_MODEL = f"{vendor}-responses/{model}"

    # strip any trailing ':.*' descriptors from model name
    MCP_TAP_MODEL = MCP_TAP_MODEL.split(":")[0]
    MCP_TAP_PLAN_MODE_MODEL = MCP_TAP_PLAN_MODE_MODEL.split(":")[0]

logging.basicConfig(
    level=getattr(logging, MCP_TAP_LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("mcptap")
COMMUNICATION_LOGGER = logging.getLogger("mcptap-communication")
COMMUNICATION_LOGGER.propagate = False
COMMUNICATION_LOGGER.setLevel(logging.INFO)
if MCP_TAP_LOG_FILE:
    communication_handler = logging.FileHandler(MCP_TAP_LOG_FILE, encoding="utf-8")
    communication_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    COMMUNICATION_LOGGER.addHandler(communication_handler)

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
}
HOP_BY_HOP_HEADERS: Set[str] = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
DEBUG_PAYLOAD_KEYS = [
    "tools",
]


def rewrite_json_payload(
    request: web.Request,
    payload: Dict[str, Any],
    intercept: "MCPInterceptor",
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

    candidate_force_model = MCP_TAP_MODEL
    reasoning = payload.get("reasoning", {})
    if not isinstance(reasoning, dict):
        reasoning = {}
    reasoning_effort = reasoning.get("effort", None)
    if MCP_TAP_PLAN_MODE_TRIGGER == reasoning_effort:
        candidate_force_model = MCP_TAP_PLAN_MODE_MODEL

    if PROVIDER_REQUESTY == MCP_TAP_UPSTREAM_PROVIDER:
        # requesty.ai does not support image_generation as a tool
        # filer out image_generation tool
        tools = []
        for tool in payload["tools"]:
            if tool["type"] != "image_generation":
                tools.append(tool)
        payload["tools"] = tools

        # some google models do not like function tools and server tools mixed
        if "google" in candidate_force_model:
            tools = []
            for tool in payload["tools"]:
                if tool["type"] == "function" or tool["type"] == "namespace":
                    tools.append(tool)
            payload["tools"] = tools
            payload["tool_config"] = {"include_server_side_tool_invocations": True}

        # some models do not like "include" field
        del payload["include"]

    if PROVIDER_OPENROUTER == MCP_TAP_UPSTREAM_PROVIDER:
        if candidate_force_model.startswith("@"):
            # openrouter.ai does not support some tools
            # in presets (model=@...)
            tools = []
            for tool in payload["tools"]:
                if tool["type"] == "function" or tool["type"] == "namespace":
                    tools.append(tool)
            payload["tools"] = tools

    if LOGGER.isEnabledFor(logging.DEBUG):
        for key in DEBUG_PAYLOAD_KEYS:
            LOGGER.debug(
                "%s %s After rewrite payload_key=%r payload_value=%r",
                request.method,
                request.path_qs,
                key,
                payload.get(key),
            )
    return original_model, forced_model, reasoning_effort


# ---------------------------------------------------------------------------
# MCP intercept manager
# ---------------------------------------------------------------------------


class InterceptedTool:
    """One tool exposed to the model that is backed by an MCP tool call."""

    def __init__(self, mapping: Dict[str, Any]) -> None:
        self.expose_as: str = mapping["expose_as"]
        self.mcp_tool: str = mapping["mcp_tool"]
        override = mapping.get("override") or {}
        if not isinstance(override, dict):
            raise RuntimeError(f"MCP intercept mapping {self.expose_as!r} has non-object 'override'")
        self.override: Dict[str, Any] = override

        # Filled by MCPInterceptor after list_tools()
        self.resolved_parameters: Optional[Dict[str, Any]] = None
        self.resolved_description: Optional[str] = None

    def to_tool_definition(self) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "type": "function",
            "execution": "client",
            "name": self.expose_as,
            "description": self.resolved_description or f"MCP-backed tool {self.mcp_tool}",
            "strict": False,
            "parameters": self.resolved_parameters or {"type": "object", "properties": {}},
        }
        # `override` shallow-merges on top of the MCP-derived definition.
        # Fields we control (type/name/execution) are always restored so the
        # routing on our side cannot be broken by a stray override.
        merged = {**base, **self.override}
        merged["type"] = "function"
        merged["execution"] = "client"
        merged["name"] = self.expose_as
        return merged


class MCPInterceptor:
    """Owns a single long-lived MCP subprocess and dispatches tool calls to it."""

    def __init__(self, config: Optional[Dict[str, Any]]) -> None:
        self._config = config
        self.tools: List[InterceptedTool] = [InterceptedTool(m) for m in config["mappings"]] if config else []
        self._session: Any = None
        self._exit_stack: Optional[contextlib.AsyncExitStack] = None
        self._lock: Optional[asyncio.Lock] = None
        self._started = False

    @property
    def enabled(self) -> bool:
        return bool(self.tools)

    def tool_names(self) -> Set[str]:
        return {tool.expose_as for tool in self.tools}

    def find_tool(self, exposed_name: str) -> Optional[InterceptedTool]:
        for tool in self.tools:
            if tool.expose_as == exposed_name:
                return tool
        return None

    async def start(self) -> None:
        if not self.enabled or self._started:
            return
        from mcp import ClientSession, StdioServerParameters  # type: ignore
        from mcp.client.stdio import stdio_client  # type: ignore

        server = self._config
        assert server is not None
        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        try:
            merged_env = os.environ.copy()
            merged_env.update(server.get("mcp_env") or {})
            params = StdioServerParameters(
                command=server["mcp_command"],
                args=list(server.get("mcp_args") or []),
                env=merged_env,
                cwd=server.get("mcp_cwd"),
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            LOGGER.info(
                "MCP session started: command=%r args=%r env=%r cwd=%r",
                server["mcp_command"],
                server.get("mcp_args") or [],
                merged_env,
                server.get("mcp_cwd"),
            )
        except Exception:
            await stack.aclose()
            raise

        self._exit_stack = stack
        self._session = session
        self._lock = asyncio.Lock()

        listing = await session.list_tools()
        by_name = {t.name: t for t in listing.tools}
        for tool in self.tools:
            mcp_tool = by_name.get(tool.mcp_tool)
            if mcp_tool is None:
                LOGGER.warning(
                    "MCP tool %r not found on server (available: %r)",
                    tool.mcp_tool,
                    list(by_name.keys()),
                )
                continue
            tool.resolved_description = mcp_tool.description
            tool.resolved_parameters = mcp_tool.inputSchema
            LOGGER.info(
                "Intercept ready: expose_as=%r -> mcp_tool=%r",
                tool.expose_as,
                tool.mcp_tool,
            )

        self._started = True

    async def stop(self) -> None:
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as exc:
                LOGGER.warning("Error closing MCP session: %s", exc)
        self._exit_stack = None
        self._session = None
        self._lock = None
        self._started = False

    async def call(self, exposed_name: str, arguments: Dict[str, Any]) -> str:
        tool = self.find_tool(exposed_name)
        if tool is None:
            raise KeyError(exposed_name)
        if self._session is None or self._lock is None:
            raise RuntimeError("MCP session is not started")
        async with self._lock:
            LOGGER.info("MCP call: expose_as=%r mcp_tool=%r", exposed_name, tool.mcp_tool)
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(tool.mcp_tool, arguments or {}),
                    timeout=MCP_TAP_INTERCEPT_TOOL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                LOGGER.warning(
                    "MCP call timed out after %.1fs: %r",
                    MCP_TAP_INTERCEPT_TOOL_TIMEOUT,
                    exposed_name,
                )
                return json.dumps({"error": f"MCP tool {tool.mcp_tool} timed out"})
            except Exception as exc:
                LOGGER.exception("MCP call failed: %s", exc)
                return json.dumps({"error": f"MCP tool {tool.mcp_tool} failed: {exc}"})
        return _serialize_mcp_result(result)


def _serialize_mcp_result(result: Any) -> str:
    """Turn an MCP CallToolResult into a plain string the model can consume."""
    if result is None:
        return ""
    if getattr(result, "structuredContent", None) is not None:
        try:
            return json.dumps(result.structuredContent, ensure_ascii=False)
        except (TypeError, ValueError):
            pass
    parts: List[str] = []
    for item in getattr(result, "content", []) or []:
        item_type = getattr(item, "type", None)
        if item_type == "text":
            parts.append(getattr(item, "text", ""))
        else:
            try:
                parts.append(json.dumps(item.model_dump(mode="json"), ensure_ascii=False))
            except Exception:
                parts.append(str(item))
    if getattr(result, "isError", False):
        prefix = "[tool error] "
    else:
        prefix = ""
    return prefix + ("\n".join(p for p in parts if p))


def _load_intercept_config() -> Optional[Dict[str, Any]]:
    """Return the intercept config as a validated dict, or None if disabled.

    MCP_TAP_INTERCEPT_YAML is a YAML object describing a single MCP server plus a
    list of mappings. Server fields (mcp_command/mcp_args/mcp_env/mcp_cwd) live
    on the top-level object; each entry in `mappings` carries `expose_as`,
    `mcp_tool`, and optionally `description`/`parameters`.

    Value can be prefixed with `@` to load YAML from a file path.
    """
    if not MCP_TAP_INTERCEPT_YAML:
        return None
    payload = MCP_TAP_INTERCEPT_YAML
    if payload.startswith("@"):
        path = payload[1:]
        with open(path, "r", encoding="utf-8") as fh:
            payload = fh.read()
    data = yaml.safe_load(payload)

    if not isinstance(data, dict):
        raise RuntimeError("MCP_TAP_INTERCEPT_YAML must be a YAML object with 'mcp_command' and 'mappings'")
    if "mcp_command" not in data:
        raise RuntimeError("MCP_TAP_INTERCEPT_YAML must contain 'mcp_command'")
    mappings = data.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise RuntimeError("MCP_TAP_INTERCEPT_YAML must contain a non-empty 'mappings' list")
    seen: Set[str] = set()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise RuntimeError("Each mapping in MCP_TAP_INTERCEPT_YAML must be an object")
        for required in ("expose_as", "mcp_tool"):
            if required not in mapping:
                raise RuntimeError(f"MCP intercept mapping missing required field: {required!r}")
        name = mapping["expose_as"]
        if name in seen:
            raise RuntimeError(f"Duplicate expose_as in MCP_TAP_INTERCEPT_YAML: {name!r}")
        seen.add(name)
    return {
        "mcp_command": data["mcp_command"],
        "mcp_args": data.get("mcp_args") or [],
        "mcp_env": data.get("mcp_env") or {},
        "mcp_cwd": data.get("mcp_cwd"),
        "mappings": mappings,
    }


# ---------------------------------------------------------------------------
# Request / response rewriting
# ---------------------------------------------------------------------------


def filtered_headers(headers) -> Dict[str, str]:
    return {name: value for name, value in headers.items() if name.lower() not in HOP_BY_HOP_HEADERS}


def _redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    if LOG_FILE_REDACT_HEADERS:
        return {
            name: "<redacted>" if name.lower() in SENSITIVE_HEADER_NAMES else value for name, value in headers.items()
        }
    return headers


def _body_to_log_text(body: Optional[bytes], direction: str) -> str:
    if not body:
        return ""

    if direction == "upstream_request":
        if LOG_PAYLOAD_KEYS:
            body_json = json.loads(body.decode("utf-8"))
            body_json = {k: v for k, v in body_json.items() if k in LOG_PAYLOAD_KEYS}
            return json.dumps(body_json, ensure_ascii=False, sort_keys=True)

        # only log keys
        body_json = json.loads(body.decode("utf-8"))
        return json.dumps(list(body_json.keys()), ensure_ascii=False, sort_keys=True)

    return body.decode("utf-8", errors="replace")


def _log_communication(
    direction: str,
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[bytes] = None,
    status: Optional[int] = None,
) -> None:
    if not MCP_TAP_LOG_FILE:
        return

    metadata = {
        "direction": direction,
        "method": method,
        "url": url,
    }
    if status is not None:
        metadata["status"] = status

    COMMUNICATION_LOGGER.info(
        "%s\nheaders=%s\nbody=%s",
        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        json.dumps(_redact_headers(headers), ensure_ascii=False, sort_keys=True),
        _body_to_log_text(body, direction),
    )


def upstream_path(incoming_path: str) -> str:
    """Map local /v1/... or /api/v1/... paths onto UPSTREAM_BASE_URL."""
    path = incoming_path or "/"
    if path == "/v1":
        return ""
    if path.startswith("/v1/"):
        return path[len("/v1") :]
    if path == "/api/v1":
        return ""
    if path.startswith("/api/v1/"):
        return path[len("/api/v1") :]

    return path if path.startswith("/") else "/" + path


def _apply_model_and_provider(
    payload: Dict[str, Any],
) -> Tuple[Optional[str], str, Optional[str]]:
    original_model = payload.get("model")
    payload["model"] = MCP_TAP_MODEL
    reasoning = payload.get("reasoning", {})
    if not isinstance(reasoning, dict):
        reasoning = {}
    reasoning_effort = reasoning.get("effort", None)
    if MCP_TAP_PLAN_MODE_TRIGGER == reasoning_effort:
        input_size = deep_getsizeof(payload.get("input", None))
        if input_size > MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE:
            raise RuntimeError(
                f"Input size ({input_size}) exceeds MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE ({MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE})"
            )
        payload["model"] = MCP_TAP_PLAN_MODE_MODEL

    if PROVIDER_OPENROUTER == MCP_TAP_UPSTREAM_PROVIDER:
        # OpenRouter's `models` parameter enables fallback to other model IDs.
        # Removing it makes MCP_TAP_MODEL the only possible model.
        payload.pop("models", None)

        provider = payload.get("provider")
        if not isinstance(provider, dict):
            provider = {}
        else:
            provider = dict(provider)

        for key in ("only", "ignore", "order", "sort", "allow_fallbacks"):
            provider.pop(key, None)

        if MCP_TAP_OPENROUTER_PROVIDER:
            provider["only"] = [MCP_TAP_OPENROUTER_PROVIDER]

        if MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS:
            provider["allow_fallbacks"] = False

        if provider:
            payload["provider"] = provider

    return original_model, payload["model"], reasoning_effort


def _inject_tools(payload: Dict[str, Any], intercept: MCPInterceptor) -> None:
    tools = payload.get("tools") or []
    if not isinstance(tools, list):
        tools = []
    # Drop incoming definitions that clash with names we own (from MCP mappings).
    # This ensures our curated definitions win.
    reserved: Set[str] = intercept.tool_names()
    tools = [t for t in tools if not (isinstance(t, dict) and t.get("name") in reserved)]
    # Append our intercepted tools (they already merge MCP schema + override).
    for tool in intercept.tools:
        tools.append(tool.to_tool_definition())
    payload["tools"] = tools


def deep_getsizeof(obj, seen=None):
    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return 0

    seen.add(obj_id)

    size = sys.getsizeof(obj)

    if isinstance(obj, dict):
        size += sum(deep_getsizeof(k, seen) for k in obj.keys())
        size += sum(deep_getsizeof(v, seen) for v in obj.values())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(deep_getsizeof(item, seen) for item in obj)
    elif hasattr(obj, "__dict__"):
        size += deep_getsizeof(vars(obj), seen)
    elif hasattr(obj, "__slots__"):
        size += sum(deep_getsizeof(getattr(obj, slot), seen) for slot in obj.__slots__ if hasattr(obj, slot))

    return size


# ---------------------------------------------------------------------------
# Response inspection: find intercepted function_call in a Responses API reply
# ---------------------------------------------------------------------------


def _iter_function_calls(response_body: Dict[str, Any]):
    """Yield (item_dict, call_id, name, arguments_str) for every function_call
    item in an OpenAI Responses-API response body."""
    output = response_body.get("output") or []
    if not isinstance(output, list):
        return
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call":
            continue
        yield (
            item,
            item.get("call_id"),
            item.get("name"),
            item.get("arguments") or "{}",
        )


def _extract_intercepted_calls(
    response_body: Dict[str, Any],
    intercept: MCPInterceptor,
) -> List[Tuple[Dict[str, Any], str, str, str]]:
    hits = []
    for item, call_id, name, arguments in _iter_function_calls(response_body):
        if name in intercept.tool_names() and call_id:
            hits.append((item, call_id, name, arguments))
    return hits


# ---------------------------------------------------------------------------
# Upstream request helpers
# ---------------------------------------------------------------------------


async def _post_upstream_buffered(
    session: ClientSession,
    path: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    stream: bool,
) -> Tuple[int, Dict[str, str], bytes, Optional[Dict[str, Any]]]:
    """Post to upstream and buffer the complete response.

    For stream=true we preserve the exact upstream SSE bytes for the client,
    but also parse response.completed so the proxy can resolve hidden MCP tool
    calls before deciding whether to replay that stream.
    """
    request_body = dict(body)
    outgoing_headers = dict(headers)
    outgoing_headers["Content-Type"] = "application/json"
    if stream:
        request_body["stream"] = True
        outgoing_headers.pop("Accept", None)
        outgoing_headers["Accept"] = "text/event-stream"
    else:
        request_body.pop("stream", None)
        outgoing_headers.pop("Accept", None)
        outgoing_headers["Accept"] = "application/json"

    url = UPSTREAM_BASE_URL + path
    data = json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _log_communication("upstream_request", "POST", url, outgoing_headers, data)
    async with session.post(
        url,
        headers=outgoing_headers,
        data=data,
        allow_redirects=False,
    ) as resp:
        raw = await resp.read()
        response_headers = filtered_headers(resp.headers)
        _log_communication("upstream_response", "POST", url, response_headers, raw, status=resp.status)

    body_json: Optional[Dict[str, Any]] = None
    if resp.status < 400:
        if stream:
            body_json = _response_json_from_sse(raw)
        else:
            try:
                candidate = json.loads(raw.decode("utf-8"))
                if isinstance(candidate, dict):
                    body_json = candidate
            except (UnicodeDecodeError, json.JSONDecodeError):
                body_json = None
    return resp.status, response_headers, raw, body_json


def _response_json_from_sse(raw: bytes) -> Optional[Dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    event_name: Optional[str] = None
    data_lines: List[str] = []
    completed: Optional[Dict[str, Any]] = None
    output_items: List[Dict[str, Any]] = []

    def flush_event() -> None:
        nonlocal event_name, data_lines, completed
        if not data_lines:
            event_name = None
            return
        data = "\n".join(data_lines)
        event_type = event_name
        event_name = None
        data_lines = []
        if data == "[DONE]":
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        payload_type = payload.get("type") or event_type
        if payload_type == "response.completed" and isinstance(payload.get("response"), dict):
            completed = payload["response"]
            return
        item = payload.get("item")
        if payload_type == "response.output_item.done" and isinstance(item, dict):
            output_items.append(item)

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            flush_event()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
    flush_event()

    if completed is not None:
        return completed
    if output_items:
        return {"output": output_items}
    return None


# ---------------------------------------------------------------------------
# Main proxy handler
# ---------------------------------------------------------------------------


async def health(_request: web.Request) -> web.Response:
    intercept: MCPInterceptor = _request.app["mcp_intercept"]
    if intercept.enabled and intercept._config is not None:
        server = intercept._config
        intercept_info: Optional[Dict[str, Any]] = {
            "mcp_command": server["mcp_command"],
            "mcp_args": server["mcp_args"],
            "mcp_cwd": server.get("mcp_cwd"),
            "mappings": [
                {
                    "expose_as": t.expose_as,
                    "mcp_tool": t.mcp_tool,
                    "resolved": t.resolved_parameters is not None,
                }
                for t in intercept.tools
            ],
        }
    else:
        intercept_info = None
    return web.json_response(
        {
            "status": "ok",
            "upstream": UPSTREAM_BASE_URL,
            "forced_model": MCP_TAP_MODEL,
            "forced_provider": MCP_TAP_OPENROUTER_PROVIDER or None,
            "provider_fallbacks_disabled": MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS,
            "mcp_intercept": intercept_info,
        }
    )


async def proxy(request: web.Request) -> web.StreamResponse:
    session: ClientSession = request.app["client_session"]
    intercept: MCPInterceptor = request.app["mcp_intercept"]
    path = upstream_path(request.path)
    target_url = UPSTREAM_BASE_URL + path
    if request.query_string:
        target_url += "?" + request.query_string

    request_headers = filtered_headers(request.headers)
    request_headers["Authorization"] = f"Bearer {MCP_TAP_API_KEY}"
    raw_body = await request.read()

    content_type = request.headers.get("Content-Type", "").lower()
    payload: Optional[Dict[str, Any]] = None
    if "application/json" in content_type and raw_body:
        try:
            candidate = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            candidate = None
        if isinstance(candidate, dict) and "model" in candidate:
            payload = candidate

    # For non-JSON or non-model bodies, keep classic passthrough.
    if payload is None:
        return await _passthrough(request, session, target_url, request_headers, raw_body)

    try:
        original_model, forced_model, reasoning_effort = rewrite_json_payload(request, payload, intercept)
    except Exception as exc:
        LOGGER.exception(exc)
        return web.json_response(
            {"error": {"message": str(exc), "type": "proxy_upstream_error"}},
            status=502,
        )

    client_wanted_stream = bool(payload.get("stream"))

    LOGGER.info(
        "%s %s model=%r -> %r reasoning_effort=%r provider=%r stream=%s intercept=%s",
        request.method,
        request.path_qs,
        original_model,
        forced_model,
        reasoning_effort,
        MCP_TAP_OPENROUTER_PROVIDER or "OpenRouter-selected",
        client_wanted_stream,
        intercept.enabled,
    )

    # Intercept loop only applies to /v1/responses POSTs. Everything else is
    # rewritten but passed through as before.
    is_responses_call = request.method == "POST" and path.rstrip("/").endswith("/responses")
    if not is_responses_call or not intercept.enabled:
        return await _forward_rewritten(
            request,
            session,
            target_url,
            request_headers,
            payload,
        )

    return await _handle_responses_with_intercept(
        request,
        session,
        path,
        request_headers,
        payload,
        intercept,
        client_wanted_stream,
    )


async def _passthrough(
    request: web.Request,
    session: ClientSession,
    target_url: str,
    request_headers: Dict[str, str],
    raw_body: bytes,
) -> web.StreamResponse:
    LOGGER.info("%s %s (body not rewritten)", request.method, request.path_qs)
    _log_communication("upstream_request", request.method, target_url, request_headers, raw_body)
    try:
        upstream_response = await session.request(
            method=request.method,
            url=target_url,
            headers=request_headers,
            data=raw_body if raw_body else None,
            allow_redirects=False,
        )
    except (ClientError, OSError) as exc:
        LOGGER.exception("Upstream request failed: %s", exc)
        return web.json_response(
            {
                "error": {
                    "message": "OpenRouter proxy could not reach the upstream API",
                    "type": "proxy_upstream_error",
                }
            },
            status=502,
        )
    response = web.StreamResponse(
        status=upstream_response.status,
        reason=upstream_response.reason,
        headers=filtered_headers(upstream_response.headers),
    )
    await response.prepare(request)
    response_body = bytearray()
    try:
        if request.method != "HEAD":
            async for chunk in upstream_response.content.iter_any():
                response_body.extend(chunk)
                await response.write(chunk)
    except (ConnectionResetError, BrokenPipeError):
        LOGGER.info("Client disconnected during streamed response")
    finally:
        _log_communication(
            "upstream_response",
            request.method,
            target_url,
            filtered_headers(upstream_response.headers),
            bytes(response_body),
            status=upstream_response.status,
        )
        upstream_response.release()
    with contextlib.suppress(ConnectionResetError, BrokenPipeError):
        await response.write_eof()
    LOGGER.info("%s %s -> HTTP %s", request.method, request.path_qs, upstream_response.status)
    return response


async def _forward_rewritten(
    request: web.Request,
    session: ClientSession,
    target_url: str,
    request_headers: Dict[str, str],
    payload: Dict[str, Any],
) -> web.StreamResponse:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    outgoing_headers = dict(request_headers)
    outgoing_headers["Content-Type"] = "application/json"
    _log_communication("upstream_request", request.method, target_url, outgoing_headers, body)
    try:
        upstream_response = await session.request(
            method=request.method,
            url=target_url,
            headers=outgoing_headers,
            data=body,
            allow_redirects=False,
        )
    except (ClientError, OSError) as exc:
        LOGGER.exception("Upstream request failed: %s", exc)
        return web.json_response(
            {
                "error": {
                    "message": "OpenRouter proxy could not reach the upstream API",
                    "type": "proxy_upstream_error",
                }
            },
            status=502,
        )
    response = web.StreamResponse(
        status=upstream_response.status,
        reason=upstream_response.reason,
        headers=filtered_headers(upstream_response.headers),
    )
    await response.prepare(request)
    response_body = bytearray()
    try:
        async for chunk in upstream_response.content.iter_any():
            response_body.extend(chunk)
            await response.write(chunk)
    except (ConnectionResetError, BrokenPipeError):
        LOGGER.info("Client disconnected during streamed response")
    finally:
        _log_communication(
            "upstream_response",
            request.method,
            target_url,
            filtered_headers(upstream_response.headers),
            bytes(response_body),
            status=upstream_response.status,
        )
        upstream_response.release()
    with contextlib.suppress(ConnectionResetError, BrokenPipeError):
        await response.write_eof()
    LOGGER.info("%s %s -> HTTP %s", request.method, request.path_qs, upstream_response.status)
    return response


async def _handle_responses_with_intercept(
    request: web.Request,
    session: ClientSession,
    path: str,
    request_headers: Dict[str, str],
    payload: Dict[str, Any],
    intercept: MCPInterceptor,
    client_wanted_stream: bool,
) -> web.StreamResponse:
    """Talk to upstream in a loop, resolving intercepted tool calls locally
    until the model returns a final response with no intercepted calls."""

    working_payload = copy.deepcopy(payload)
    working_payload.pop("stream", None)

    # `input` in Responses API is either a string or a list of items. We must
    # append function_call + function_call_output turns as items, so promote a
    # string input to a list first.
    def _ensure_input_list() -> List[Any]:
        raw_input = working_payload.get("input")
        if raw_input is None:
            working_payload["input"] = []
        elif isinstance(raw_input, str):
            working_payload["input"] = [{"role": "user", "content": [{"type": "input_text", "text": raw_input}]}]
        elif not isinstance(raw_input, list):
            working_payload["input"] = [raw_input]
        return working_payload["input"]

    last_status = 200
    last_headers: Dict[str, str] = {}
    last_body_raw: bytes = b""

    for iteration in range(MCP_TAP_INTERCEPT_MAX_ITERATIONS):
        try:
            status, resp_headers, raw, body_json = await _post_upstream_buffered(
                session,
                path,
                request_headers,
                working_payload,
                stream=client_wanted_stream,
            )
        except (ClientError, OSError) as exc:
            LOGGER.exception("Upstream request failed: %s", exc)
            return web.json_response(
                {
                    "error": {
                        "message": "OpenRouter proxy could not reach the upstream API",
                        "type": "proxy_upstream_error",
                    }
                },
                status=502,
            )

        last_status = status
        last_headers = resp_headers
        last_body_raw = raw

        if status >= 400:
            LOGGER.warning(
                "Upstream returned HTTP %s during intercept loop iteration=%d",
                status,
                iteration,
            )
            break

        if body_json is None:
            LOGGER.warning("Upstream response could not be parsed during intercept loop; forwarding as-is")
            break

        hits = _extract_intercepted_calls(body_json, intercept)
        if not hits:
            break

        LOGGER.info(
            "Intercept iteration=%d hits=%d names=%r",
            iteration,
            len(hits),
            [name for _, _, name, _ in hits],
        )

        # Continue with explicit input items. This is more reliable than
        # previous_response_id through OpenRouter, especially when store=false.
        _ensure_input_list()
        working_payload.pop("previous_response_id", None)
        for out_item in body_json.get("output") or []:
            if isinstance(out_item, dict):
                working_payload["input"].append(out_item)

        for _, call_id, name, arguments in hits:
            try:
                parsed_args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
                if not isinstance(parsed_args, dict):
                    parsed_args = {}
            except json.JSONDecodeError:
                parsed_args = {}
            output_text = await intercept.call(name, parsed_args)
            working_payload["input"].append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_text,
                }
            )
    else:
        LOGGER.warning(
            "Intercept loop reached MCP_TAP_INTERCEPT_MAX_ITERATIONS=%d without a final answer",
            MCP_TAP_INTERCEPT_MAX_ITERATIONS,
        )

    # Build the client-facing response. For stream=true this replays the exact
    # final upstream SSE bytes; no synthetic stream is generated.
    return await _emit_buffered_response(
        request,
        status=last_status,
        headers=last_headers,
        raw=last_body_raw,
    )


async def _emit_buffered_response(
    request: web.Request,
    status: int,
    headers: Dict[str, str],
    raw: bytes,
) -> web.StreamResponse:
    response_headers = dict(headers)
    # ClientSession auto_decompress=True returns decoded bytes, so keep the
    # client-facing headers consistent with the raw body we actually send.
    response_headers.pop("Content-Encoding", None)
    response_headers.pop("Content-Length", None)
    if raw and not response_headers.get("Content-Type"):
        response_headers["Content-Type"] = "application/json"
    response = web.StreamResponse(status=status, headers=response_headers)
    await response.prepare(request)
    with contextlib.suppress(ConnectionResetError, BrokenPipeError):
        await response.write(raw)
        await response.write_eof()
    LOGGER.info("%s %s -> HTTP %s", request.method, request.path_qs, status)
    return response


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


async def create_client_session(app: web.Application) -> None:
    timeout = ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=None)
    connector = TCPConnector(limit=100, ttl_dns_cache=300)
    app["client_session"] = ClientSession(
        timeout=timeout,
        connector=connector,
        auto_decompress=True,
    )


async def close_client_session(app: web.Application) -> None:
    await app["client_session"].close()


async def start_mcp_intercept(app: web.Application) -> None:
    intercept: MCPInterceptor = app["mcp_intercept"]
    if not intercept.enabled:
        LOGGER.info("MCP intercept disabled (MCP_TAP_INTERCEPT_YAML is empty)")
        return
    try:
        await intercept.start()
    except Exception:
        LOGGER.exception("Failed to start MCP intercept; continuing without interception")


async def stop_mcp_intercept(app: web.Application) -> None:
    intercept: MCPInterceptor = app.get("mcp_intercept")
    if intercept is not None:
        await intercept.stop()


def build_app() -> web.Application:
    app = web.Application(client_max_size=100 * 1024 * 1024)
    try:
        intercept_config = _load_intercept_config()
    except Exception:
        LOGGER.exception("Invalid MCP_TAP_INTERCEPT_YAML; disabling intercept")
        intercept_config = None
    app["mcp_intercept"] = MCPInterceptor(intercept_config)
    app.on_startup.append(create_client_session)
    app.on_startup.append(start_mcp_intercept)
    app.on_cleanup.append(stop_mcp_intercept)
    app.on_cleanup.append(close_client_session)
    app.router.add_get("/health", health)
    app.router.add_route("*", "/{tail:.*}", proxy)
    return app


def main() -> None:
    LOGGER.info(
        "Listening on http://%s:%s; upstream=%s; forced_model=%s; forced_plan_mode_model=%s; forced_provider=%s",
        MCP_TAP_LISTEN_HOST,
        MCP_TAP_LISTEN_PORT,
        UPSTREAM_BASE_URL,
        MCP_TAP_MODEL,
        MCP_TAP_PLAN_MODE_MODEL,
        MCP_TAP_OPENROUTER_PROVIDER or "OpenRouter-selected",
    )
    web.run_app(
        build_app(),
        host=MCP_TAP_LISTEN_HOST,
        port=MCP_TAP_LISTEN_PORT,
        access_log=None,
        handle_signals=True,
    )


if __name__ == "__main__":
    main()
