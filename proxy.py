#!/usr/bin/env python3
"""Transparent LLM proxy for the OpenAI Responses API.

Forces a single model on every request, with optional plan-mode switching
based on reasoning effort. Supports OpenRouter and Requesty as upstream
providers, with provider pinning and fallback control.

Features
--------
- **Model forcing**: every request is rewritten to use MCP_TAP_MODEL (or
  MCP_TAP_PLAN_MODE_MODEL when the reasoning effort matches
  MCP_TAP_PLAN_MODE_TRIGGER).
- **Provider selection**: upstream is OpenRouter or Requesty, selected via
  MCP_TAP_UPSTREAM_PROVIDER. OpenRouter provider pinning and fallback
  disabling are configurable.
- **Per-model instructions**: inject additional system instructions per model
  via MCP_TAP_PER_MODEL_YAML (inline YAML or @path to a file).
- **MCP tool interception**: tools declared in MCP_TAP_INTERCEPT_YAML are
  exposed to the model as regular function tools. When the model calls them,
  the proxy executes the real MCP tool locally, feeds the result back into the
  conversation, and only surfaces the final assistant response. The client
  never sees the intercepted tool calls.
- **Tool-call hook gateway**: when MCP_TAP_USE_TOOL_HOOK points to a hook
  script, the proxy intercepts client tool calls, injects a synthetic
  ``get_goal`` call, and runs the hook to allow or block the response.
  The hook can also return ``updated_tool_calls`` to rewrite tool call
  arguments (e.g. wrap shell commands with ``rtk``) before the response
  is returned to the client.
- **Session tracking**: UUIDv7-based session tracking with token accumulation
  and elapsed-time measurement per conversation.
- **File logging**: optional request/response logging to MCP_TAP_LOG_FILE.

Compatible with Python 3.10+.
Supports normal JSON responses and streaming SSE responses.

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
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml  # type: ignore
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
MCP_TAP_PER_MODEL_YAML = os.environ.get("MCP_TAP_PER_MODEL_YAML", "").strip()
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

MCP_TAP_USE_TOOL_HOOK = os.environ.get("MCP_TAP_USE_TOOL_HOOK", "").strip()
MCP_TAP_USE_TOOL_HOOK_TIMEOUT = float(os.environ.get("MCP_TAP_USE_TOOL_HOOK_TIMEOUT", "30"))

# When set, the proxy injects a synthetic call to this tool name before
# running the hook.  When empty, the hook runs immediately upon detecting
# client tool calls, without injecting any synthetic tool.
MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL = os.environ.get("MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL", "get_goal").strip()

# Directory for per-session blocklist control files.  Each session gets a
# subdirectory named after the Codex session ID (CODEX_THREAD_ID).
# The LD_PRELOAD library reads <dir>/<session_id>/blocked_files.
MCP_TAP_PER_SESSION_DIR = os.environ.get("MCP_TAP_PER_SESSION_DIR", "/tmp/mcptap/per_session").strip()

# Maximum age (seconds) for a pending state before it is cleaned up.
MCP_TAP_USE_TOOL_HOOK_PENDING_TTL = 600.0

SYNTHETIC_GET_GOAL_CALL_ID = "synthetic_get_goal"
SYNTHETIC_GET_GOAL_TOOL_NAME = "get_goal"

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


def _load_per_model_config() -> Dict[str, Dict[str, Any]]:
    """Load per-model configuration from MCP_TAP_PER_MODEL_YAML.

    Returns a dict mapping model identifiers to their config (e.g. instructions).
    Supports model names with suffixes like ':floor' (suffix is ignored for matching).
    Also supports '@preset/name' and 'policy/name' entries.
    """
    if not MCP_TAP_PER_MODEL_YAML:
        return {}

    payload = MCP_TAP_PER_MODEL_YAML
    if payload.startswith("@"):
        path = payload[1:]
        with open(path, "r", encoding="utf-8") as fh:
            payload = fh.read()

    data = yaml.safe_load(payload)
    if not isinstance(data, dict):
        LOGGER.warning("MCP_TAP_PER_MODEL_YAML must be a YAML dict")
        return {}

    # Process entries - strip suffixes from model names for matching
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


def rewrite_json_payload(
    request: web.Request,
    payload: Dict[str, Any],
    intercept: "MCPInterceptor",
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
# Session tracking and tool-call hook gateway
# ---------------------------------------------------------------------------


def _uuid_v7_timestamp(uuid_str: str) -> Optional[float]:
    """Extract a Unix timestamp (seconds) from a UUIDv7 string.

    UUIDv7 embeds a 48-bit Unix timestamp in milliseconds in the first 48 bits.
    Returns None if the string is not a valid UUIDv7.
    """
    try:
        u = uuid.UUID(uuid_str)
    except (ValueError, AttributeError):
        return None
    if u.version != 7:
        return None
    bits = u.int
    timestamp_ms = bits >> 80
    return timestamp_ms / 1000.0


class SessionTracker:
    """Tracks per-session token usage and session start time.

    Session ID is extracted from the ``session-id`` header.
    If the UUID is v7, the embedded timestamp is used as session start;
    otherwise the time of the first request is used.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def track_request(self, request: web.Request, forced_model: str) -> Dict[str, Any]:
        """Return (or create) the session info dict for this request."""
        session_id = request.headers.get("session-id", "").strip()
        if not session_id:
            session_id = "default"

        async with self._lock:
            info = self._sessions.get(session_id)
            if info is None:
                ts = _uuid_v7_timestamp(session_id)
                start_time = ts if ts is not None else time.time()
                info = {
                    "session_id": session_id,
                    "start_time": start_time,
                    "total_tokens": 0,
                    "forced_model": forced_model,
                }
                self._sessions[session_id] = info
            else:
                info["forced_model"] = forced_model
            return dict(info)

    async def add_usage(self, session_id: str, total_tokens: int) -> None:
        if not session_id or total_tokens <= 0:
            return
        async with self._lock:
            info = self._sessions.get(session_id)
            if info is None:
                ts = _uuid_v7_timestamp(session_id)
                start_time = ts if ts is not None else time.time()
                info = {
                    "session_id": session_id,
                    "start_time": start_time,
                    "total_tokens": 0,
                    "forced_model": MCP_TAP_MODEL,
                }
                self._sessions[session_id] = info
            info["total_tokens"] += total_tokens

    async def get_usage(self, session_id: str) -> int:
        async with self._lock:
            info = self._sessions.get(session_id)
            return info["total_tokens"] if info else 0

    async def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            info = self._sessions.get(session_id)
            return dict(info) if info else None

    async def get_elapsed_seconds(self, session_id: str) -> float:
        async with self._lock:
            info = self._sessions.get(session_id)
            if info is None:
                return 0.0
            return time.time() - info["start_time"]

    async def cleanup_expired(self) -> None:
        now = time.time()
        async with self._lock:
            expired = [sid for sid, info in self._sessions.items() if now - info["start_time"] > 3600]
            for sid in expired:
                del self._sessions[sid]


class PendingState:
    """Stores a saved upstream response with client function calls awaiting hook decision."""

    def __init__(
        self,
        session_id: str,
        saved_status: int,
        saved_headers: Dict[str, str],
        saved_raw: bytes,
        saved_body_json: Dict[str, Any],
        client_tool_calls: List[Dict[str, Any]],
        get_goal_result: Dict[str, Any],
        forced_model: str,
        used_tokens: int,
        used_time_seconds: float,
    ) -> None:
        self.session_id = session_id
        self.saved_status = saved_status
        self.saved_headers = saved_headers
        self.saved_raw = saved_raw
        self.saved_body_json = saved_body_json
        self.client_tool_calls = client_tool_calls
        self.get_goal_result = get_goal_result
        self.forced_model = forced_model
        self.used_tokens = used_tokens
        self.used_time_seconds = used_time_seconds
        self.created_at = time.time()

    def is_expired(self, ttl: float = MCP_TAP_USE_TOOL_HOOK_PENDING_TTL) -> bool:
        return time.time() - self.created_at > ttl


class ToolHookGateway:
    """Manages pending tool-call batches and runs the hook script.

    When the model returns client function calls (non-intercepted), the gateway:
    1. Saves the upstream response.
    2. Returns a synthetic ``get_goal`` call to the client.
    3. On the next request (which contains the get_goal result), runs the hook.
    4. Returns the saved response if allowed, or feeds the block message to the model.
    """

    def __init__(self, session_tracker: SessionTracker) -> None:
        self.enabled = bool(MCP_TAP_USE_TOOL_HOOK)
        self.session_tracker = session_tracker
        self._pending: Dict[str, PendingState] = {}
        self._lock = asyncio.Lock()

    async def get_pending(self, session_id: str) -> Optional[PendingState]:
        async with self._lock:
            state = self._pending.get(session_id)
            if state is None:
                return None
            if state.is_expired():
                del self._pending[session_id]
                return None
            return state

    async def set_pending(self, session_id: str, state: PendingState) -> None:
        async with self._lock:
            now = time.time()
            expired = [
                sid for sid, s in self._pending.items() if now - s.created_at > MCP_TAP_USE_TOOL_HOOK_PENDING_TTL
            ]
            for sid in expired:
                del self._pending[sid]
            self._pending[session_id] = state

    async def clear_pending(self, session_id: str) -> None:
        async with self._lock:
            self._pending.pop(session_id, None)

    async def run_hook(self, state: PendingState) -> Dict[str, Any]:
        """Run the hook script and return its decision.

        Returns one of:
            {"action": "allow"}
            {"action": "allow", "blocked_files": [...]}
            {"action": "allow", "updated_tool_calls": [...]}
            {"action": "block", "message": "..."}

        ``updated_tool_calls`` lets the hook rewrite tool call arguments
        (e.g. ``cmd`` field) before the response is returned to the client.
        Each entry must contain a ``call_id`` and may override ``name``
        and/or ``arguments``.

        On timeout, non-zero exit, or invalid JSON, raises RuntimeError.
        """
        hook_input = {
            "session_id": state.session_id,
            "forced_model": state.forced_model,
            "used_tokens": state.used_tokens,
            "used_time_seconds": state.used_time_seconds,
            "get_goal_result": state.get_goal_result,
            "tool_calls": state.client_tool_calls,
        }
        stdin_data = json.dumps(hook_input, ensure_ascii=False)

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                MCP_TAP_USE_TOOL_HOOK,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RuntimeError(f"Failed to start hook script: {exc}") from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(stdin_data.encode("utf-8")),
                timeout=MCP_TAP_USE_TOOL_HOOK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"Hook script timed out after {MCP_TAP_USE_TOOL_HOOK_TIMEOUT}s")

        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")[:2000]
            raise RuntimeError(f"Hook script exited with code {proc.returncode}: {stderr_text}")

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        try:
            result = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Hook script returned invalid JSON: {stdout_text[:500]}") from exc

        if not isinstance(result, dict) or result.get("action") not in ("allow", "block"):
            raise RuntimeError(
                f'Hook script must return {{"action": "allow"}} or '
                f'{{"action": "block", "message": "..."}}, got: {stdout_text[:500]}'
            )

        return result


def _extract_usage_total_tokens(body_json: Optional[Dict[str, Any]]) -> int:
    """Extract usage.total_tokens from a Responses API response body."""
    if not body_json:
        return 0
    usage = body_json.get("usage")
    if not isinstance(usage, dict):
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, (int, float)):
        return int(total)
    return 0


def _extract_client_tool_calls(
    body_json: Dict[str, Any],
    intercept_names: Set[str],
) -> List[Dict[str, Any]]:
    """Extract function_call items that are NOT intercepted MCP tools.

    Returns a list of dicts with keys: call_id, name, arguments.
    """
    result = []
    for item, call_id, name, arguments in _iter_function_calls(body_json):
        if name in intercept_names:
            continue
        if not call_id:
            continue
        try:
            parsed_args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
            if not isinstance(parsed_args, dict):
                parsed_args = {}
        except json.JSONDecodeError:
            parsed_args = {}
        result.append(
            {
                "call_id": call_id,
                "name": name,
                "arguments": parsed_args,
            }
        )
    return result


def _has_intercepted_calls(body_json: Dict[str, Any], intercept_names: Set[str]) -> bool:
    """Check if the response contains any intercepted MCP tool calls."""
    for _, call_id, name, _ in _iter_function_calls(body_json):
        if name in intercept_names and call_id:
            return True
    return False


def _has_client_tool_calls(body_json: Dict[str, Any], intercept_names: Set[str]) -> bool:
    """Check if the response contains any client (non-intercepted) function calls."""
    for _, call_id, name, _ in _iter_function_calls(body_json):
        if name not in intercept_names and call_id:
            return True
    return False


def _extract_get_goal_result(working_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the get_goal result from the input items sent back by the client.

    The client executes the synthetic get_goal call and returns a
    function_call_output item with call_id == SYNTHETIC_GET_GOAL_CALL_ID.
    """
    input_items = working_payload.get("input") or []
    if not isinstance(input_items, list):
        return {}
    for item in input_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call_output" and item.get("call_id") == SYNTHETIC_GET_GOAL_CALL_ID:
            output = item.get("output")
            if isinstance(output, dict):
                return output
            if isinstance(output, str):
                try:
                    parsed = json.loads(output)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
            return {"output": output}
    return {}


def _strip_synthetic_get_goal(input_items: List[Any]) -> List[Any]:
    """Remove synthetic get_goal function_call and its function_call_output from input items."""
    result = []
    synthetic_call_ids = {SYNTHETIC_GET_GOAL_CALL_ID}
    for item in input_items:
        if not isinstance(item, dict):
            result.append(item)
            continue
        call_id = item.get("call_id")
        if call_id in synthetic_call_ids:
            continue
        result.append(item)
    return result


def _build_synthetic_tool_response(
    forced_model: str,
    tool_name: str,
) -> Dict[str, Any]:
    """Build a synthetic response containing a single function_call to the
    given tool name.  The client executes the tool and sends the result back,
    which MCPTap intercepts to run the hook.
    """
    return {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(time.time()),
        "model": forced_model,
        "status": "incompleted",
        "output": [
            {
                "type": "function_call",
                "id": f"fc_{uuid.uuid4().hex[:24]}",
                "call_id": SYNTHETIC_GET_GOAL_CALL_ID,
                "name": tool_name,
                "arguments": "{}",
            }
        ],
        "usage": None,
    }


def _build_synthetic_get_goal_response(
    forced_model: str,
) -> Dict[str, Any]:
    """Build a synthetic response containing a single get_goal function_call."""
    return _build_synthetic_tool_response(forced_model, SYNTHETIC_GET_GOAL_TOOL_NAME)


def _build_hook_error_response(error_message: str, forced_model: str) -> Dict[str, Any]:
    """Build an error response for use_tool_hook_error."""
    return {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(time.time()),
        "model": forced_model,
        "status": "failed",
        "error": {
            "message": error_message,
            "type": "use_tool_hook_error",
        },
        "output": [],
        "usage": None,
    }


def _build_sse_from_response(response: Dict[str, Any]) -> bytes:
    """Build a minimal SSE byte stream from a response dict.

    Emits response.created, response.output_item.added, response.output_item.done
    and response.completed events so the client can parse the response and
    extract individual output items (e.g. function_call items).
    """
    lines: List[str] = []
    created_payload = {"type": "response.created", "response": response}
    lines.append("event: response.created")
    lines.append(f"data: {json.dumps(created_payload, ensure_ascii=False)}")
    lines.append("")

    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        added_payload = {"type": "response.output_item.added", "item": item}
        lines.append("event: response.output_item.added")
        lines.append(f"data: {json.dumps(added_payload, ensure_ascii=False)}")
        lines.append("")
        done_payload = {"type": "response.output_item.done", "item": item}
        lines.append("event: response.output_item.done")
        lines.append(f"data: {json.dumps(done_payload, ensure_ascii=False)}")
        lines.append("")

    completed_payload = {"type": "response.completed", "response": response}
    lines.append("event: response.completed")
    lines.append(f"data: {json.dumps(completed_payload, ensure_ascii=False)}")
    lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# File access blocking (LD_PRELOAD integration)
# ---------------------------------------------------------------------------


def _blocklist_file_path(session_id: str) -> str:
    """Return the path to the per-session blocklist control file.

    The LD_PRELOAD library reads this file to know which paths to block.
    The path is: <MCP_TAP_PER_SESSION_DIR>/<session_id>/blocked_files
    """
    session_dir = os.path.join(MCP_TAP_PER_SESSION_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    return os.path.join(session_dir, "blocked_files")


def _write_blocklist(session_id: str, blocked_files: List[str]) -> str:
    """Write the blocked files list to a control file and return its path."""
    path = _blocklist_file_path(session_id)
    with open(path, "w") as f:
        for entry in blocked_files:
            f.write(f"{entry}\n")
    LOGGER.info(
        "Blocklist written for session=%s: %d files -> %s",
        session_id,
        len(blocked_files),
        path,
    )
    return path


def _clear_blocklist(session_id: str) -> None:
    """Remove the blocklist control file for a session."""
    path = _blocklist_file_path(session_id)
    try:
        os.unlink(path)
        LOGGER.info("Blocklist cleared for session=%s", session_id)
    except FileNotFoundError:
        pass


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


def _apply_tool_call_updates(
    body_json: Dict[str, Any],
    updated_tool_calls: List[Dict[str, Any]],
) -> bool:
    """Apply updated arguments to function_call items in the response body.

    Each entry in ``updated_tool_calls`` must have a ``call_id`` and may
    override ``name`` and/or ``arguments``.  The ``arguments`` field is
    stored as a JSON string inside function_call items.

    Returns ``True`` if any item was modified, ``False`` otherwise.
    """
    if not updated_tool_calls:
        return False

    updates_by_id: Dict[str, Dict[str, Any]] = {}
    for upd in updated_tool_calls:
        call_id = upd.get("call_id")
        if not call_id:
            continue
        updates_by_id[call_id] = upd

    if not updates_by_id:
        return False

    output = body_json.get("output") or []
    if not isinstance(output, list):
        return False

    modified = False
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call":
            continue
        call_id = item.get("call_id")
        if call_id not in updates_by_id:
            continue
        upd = updates_by_id[call_id]
        if "name" in upd and upd["name"]:
            item["name"] = upd["name"]
            modified = True
        if "arguments" in upd:
            args_val = upd["arguments"]
            if isinstance(args_val, (dict, list)):
                item["arguments"] = json.dumps(args_val, ensure_ascii=False)
            else:
                item["arguments"] = str(args_val)
            modified = True

    return modified


def _re_serialize_response(
    saved_body_json: Dict[str, Any],
    client_wanted_stream: bool,
) -> bytes:
    """Re-serialize a response body to bytes for the client.

    For non-stream responses this produces a JSON byte string.  For stream
    responses this builds a minimal SSE byte stream via
    ``_build_sse_from_response``.
    """
    if client_wanted_stream:
        return _build_sse_from_response(saved_body_json)
    return json.dumps(saved_body_json, ensure_ascii=False).encode("utf-8")


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

    per_model_config: Dict[str, Dict[str, Any]] = _request.app["per_model_config"]

    return web.json_response(
        {
            "status": "ok",
            "upstream": UPSTREAM_BASE_URL,
            "forced_model": MCP_TAP_MODEL,
            "forced_provider": MCP_TAP_OPENROUTER_PROVIDER or None,
            "provider_fallbacks_disabled": MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS,
            "mcp_intercept": intercept_info,
            "per_model_config": per_model_config,
            "use_tool_hook": {
                "enabled": bool(MCP_TAP_USE_TOOL_HOOK),
                "hook_script": MCP_TAP_USE_TOOL_HOOK or None,
                "timeout": MCP_TAP_USE_TOOL_HOOK_TIMEOUT,
            },
        }
    )


async def proxy(request: web.Request) -> web.StreamResponse:
    session: ClientSession = request.app["client_session"]
    intercept: MCPInterceptor = request.app["mcp_intercept"]
    per_model_config: Dict[str, Dict[str, Any]] = request.app["per_model_config"]
    hook_gateway: ToolHookGateway = request.app["hook_gateway"]
    session_tracker: SessionTracker = request.app["session_tracker"]
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
        original_model, forced_model, reasoning_effort = rewrite_json_payload(
            request,
            payload,
            intercept,
            per_model_config,
        )
    except Exception as exc:
        LOGGER.exception(exc)
        return web.json_response(
            {"error": {"message": str(exc), "type": "proxy_upstream_error"}},
            status=502,
        )

    client_wanted_stream = bool(payload.get("stream"))

    LOGGER.info(
        "%s %s model=%r -> %r reasoning_effort=%r provider=%r stream=%s intercept=%s hook=%s",
        request.method,
        request.path_qs,
        original_model,
        forced_model,
        reasoning_effort,
        MCP_TAP_OPENROUTER_PROVIDER or "OpenRouter-selected",
        client_wanted_stream,
        intercept.enabled,
        hook_gateway.enabled,
    )

    # The intercept/hook loop applies to /v1/responses POSTs when either MCP
    # tool interception or the tool-call hook is enabled. The two features are
    # independent: the hook can gate client tool calls (e.g. shell/exec_command)
    # even when no MCP intercept config is present. Everything else is
    # rewritten and passed through as before.
    is_responses_call = request.method == "POST" and path.rstrip("/").endswith("/responses")
    if not is_responses_call or not (intercept.enabled or hook_gateway.enabled):
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
        hook_gateway,
        session_tracker,
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
    hook_gateway: ToolHookGateway,
    session_tracker: SessionTracker,
) -> web.StreamResponse:
    """Talk to upstream in a loop, resolving intercepted tool calls locally
    until the model returns a final response with no intercepted calls.

    When MCP_TAP_USE_TOOL_HOOK is configured, batches of client function calls
    are intercepted: a synthetic get_goal call is returned to the client, and
    on the next request the hook script decides whether to allow or block.
    """

    session_id = request.headers.get("session-id", "").strip() or "default"
    forced_model = payload.get("model", MCP_TAP_MODEL)

    # Track session
    session_info = await session_tracker.track_request(request, forced_model)
    session_id = session_info["session_id"]

    # Check for a pending hook state from a previous synthetic get_goal
    pending_state = await hook_gateway.get_pending(session_id) if hook_gateway.enabled else None

    working_payload = copy.deepcopy(payload)
    working_payload.pop("stream", None)

    # `input` in Responses API is either a string or a list of items. We must
    # append function_call + function_call_output turns as items, so promote a
    # string input to a list first.
    def _ensure_input_list(pl: Optional[Dict[str, Any]] = None) -> List[Any]:
        target = pl if pl is not None else working_payload
        raw_input = target.get("input")
        if raw_input is None:
            target["input"] = []
        elif isinstance(raw_input, str):
            target["input"] = [{"role": "user", "content": [{"type": "input_text", "text": raw_input}]}]
        elif not isinstance(raw_input, list):
            target["input"] = [raw_input]
        return target["input"]

    # If there is a pending hook state, the client just returned the get_goal result.
    if pending_state is not None:
        return await _handle_hook_decision(
            request,
            session,
            path,
            request_headers,
            working_payload,
            intercept,
            client_wanted_stream,
            hook_gateway,
            session_tracker,
            session_id,
            pending_state,
            _ensure_input_list,
        )

    last_status = 200
    last_headers: Dict[str, str] = {}
    last_body_raw: bytes = b""

    intercept_names = intercept.tool_names() if intercept.enabled else set()

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

        # Accumulate token usage
        tokens = _extract_usage_total_tokens(body_json)
        if tokens > 0:
            await session_tracker.add_usage(session_id, tokens)

        has_intercepted = _has_intercepted_calls(body_json, intercept_names)
        has_client = _has_client_tool_calls(body_json, intercept_names)

        # If there are mixed calls, resolve intercepted ones first, then defer client calls.
        if has_intercepted:
            hits = _extract_intercepted_calls(body_json, intercept)
            LOGGER.info(
                "Intercept iteration=%d hits=%d names=%r",
                iteration,
                len(hits),
                [name for _, _, name, _ in hits],
            )

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

            # After resolving intercepted calls, loop again to check if the
            # model now produces client calls or more intercepted calls.
            continue

        # No intercepted calls. Check for client tool calls that need the hook.
        if has_client and hook_gateway.enabled:
            client_calls = _extract_client_tool_calls(body_json, intercept_names)
            if client_calls:
                used_tokens = await session_tracker.get_usage(session_id)
                used_time = await session_tracker.get_elapsed_seconds(session_id)

                if MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL:
                    # Synthetic tool mode: inject a synthetic call (e.g. get_goal)
                    # to gather additional context before running the hook.
                    state = PendingState(
                        session_id=session_id,
                        saved_status=status,
                        saved_headers=resp_headers,
                        saved_raw=raw,
                        saved_body_json=body_json,
                        client_tool_calls=client_calls,
                        get_goal_result={},
                        forced_model=forced_model,
                        used_tokens=used_tokens,
                        used_time_seconds=used_time,
                    )
                    await hook_gateway.set_pending(session_id, state)

                    LOGGER.info(
                        "Tool hook: deferring %d client tool calls for session=%s; returning synthetic %s",
                        len(client_calls),
                        session_id,
                        MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL,
                    )

                    # Build and return the synthetic tool response
                    synthetic = _build_synthetic_tool_response(
                        forced_model,
                        MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL,
                    )
                    if client_wanted_stream:
                        sse_raw = _build_sse_from_response(synthetic)
                        return await _emit_buffered_response(
                            request,
                            status=200,
                            headers={},
                            raw=sse_raw,
                        )
                    else:
                        return await _emit_buffered_response(
                            request,
                            status=200,
                            headers={"Content-Type": "application/json"},
                            raw=json.dumps(synthetic, ensure_ascii=False).encode("utf-8"),
                        )
                else:
                    # Direct hook mode: run the hook immediately without
                    # injecting a synthetic tool call.
                    state = PendingState(
                        session_id=session_id,
                        saved_status=status,
                        saved_headers=resp_headers,
                        saved_raw=raw,
                        saved_body_json=body_json,
                        client_tool_calls=client_calls,
                        get_goal_result={},
                        forced_model=forced_model,
                        used_tokens=used_tokens,
                        used_time_seconds=used_time,
                    )
                    return await _handle_direct_hook(
                        request,
                        session,
                        path,
                        request_headers,
                        working_payload,
                        intercept,
                        client_wanted_stream,
                        hook_gateway,
                        session_tracker,
                        session_id,
                        state,
                        _ensure_input_list,
                    )

        # No intercepted calls, no client calls needing hook, or hook disabled.
        break
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


async def _handle_direct_hook(
    request: web.Request,
    session: ClientSession,
    path: str,
    request_headers: Dict[str, str],
    working_payload: Dict[str, Any],
    intercept: MCPInterceptor,
    client_wanted_stream: bool,
    hook_gateway: ToolHookGateway,
    session_tracker: SessionTracker,
    session_id: str,
    pending_state: PendingState,
    ensure_input_list_fn,
) -> web.StreamResponse:
    """Run the hook immediately without injecting a synthetic tool call.

    The hook receives the tool calls and session context directly, with no
    synthetic tool result.  On ``allow``, the saved upstream response is
    returned (optionally with file-block instructions).  On ``block``, the
    block message is fed to the model in a follow-up upstream request.
    """

    # Run the hook script
    try:
        decision = await hook_gateway.run_hook(pending_state)
    except RuntimeError as exc:
        LOGGER.error("Tool hook error for session=%s: %s", session_id, exc)
        error_resp = _build_hook_error_response(str(exc), pending_state.forced_model)
        if client_wanted_stream:
            sse_raw = _build_sse_from_response(error_resp)
            return await _emit_buffered_response(request, status=200, headers={}, raw=sse_raw)
        else:
            return await _emit_buffered_response(
                request,
                status=200,
                headers={"Content-Type": "application/json"},
                raw=json.dumps(error_resp, ensure_ascii=False).encode("utf-8"),
            )

    action = decision.get("action")

    if action == "allow":
        blocked_files = decision.get("blocked_files", [])
        updated_tool_calls = decision.get("updated_tool_calls", [])
        LOGGER.info(
            "Tool hook allowed tool calls for session=%s (blocked_files=%d, updated_tool_calls=%d)",
            session_id,
            len(blocked_files),
            len(updated_tool_calls),
        )

        if blocked_files:
            _write_blocklist(session_id, blocked_files)

        response_raw = pending_state.saved_raw
        if updated_tool_calls:
            modified = _apply_tool_call_updates(pending_state.saved_body_json, updated_tool_calls)
            if modified:
                response_raw = _re_serialize_response(pending_state.saved_body_json, client_wanted_stream)
                LOGGER.info(
                    "Tool hook applied %d tool call updates for session=%s",
                    len(updated_tool_calls),
                    session_id,
                )

        return await _emit_buffered_response(
            request,
            status=pending_state.saved_status,
            headers=pending_state.saved_headers,
            raw=response_raw,
        )

    # action == "block"
    block_message = decision.get("message", "Tool calls blocked by hook")
    LOGGER.info("Tool hook blocked tool calls for session=%s: %s", session_id, block_message)

    # Feed the block message to the model and pass through once without hook.
    ensure_input_list_fn(working_payload)

    # Add the original model output items (function calls) back
    for out_item in pending_state.saved_body_json.get("output") or []:
        if isinstance(out_item, dict):
            working_payload["input"].append(out_item)

    # Add the block message as a system instruction
    existing_instructions = working_payload.get("instructions", "") or ""
    block_instruction = f"\n\n[TOOL CALL BLOCKED] {block_message}"
    working_payload["instructions"] = existing_instructions + block_instruction

    # Make one upstream request with pass-through (no hook)
    working_payload.pop("previous_response_id", None)

    try:
        status, resp_headers, raw, body_json = await _post_upstream_buffered(
            session,
            path,
            request_headers,
            working_payload,
            stream=client_wanted_stream,
        )
    except (ClientError, OSError) as exc:
        LOGGER.exception("Upstream request failed after block: %s", exc)
        return web.json_response(
            {
                "error": {
                    "message": "OpenRouter proxy could not reach the upstream API",
                    "type": "proxy_upstream_error",
                }
            },
            status=502,
        )

    # Accumulate tokens from the post-block response
    if body_json:
        tokens = _extract_usage_total_tokens(body_json)
        if tokens > 0:
            await session_tracker.add_usage(session_id, tokens)

    # Pass through this response without running the hook again
    return await _emit_buffered_response(
        request,
        status=status,
        headers=resp_headers,
        raw=raw,
    )


async def _handle_hook_decision(
    request: web.Request,
    session: ClientSession,
    path: str,
    request_headers: Dict[str, str],
    working_payload: Dict[str, Any],
    intercept: MCPInterceptor,
    client_wanted_stream: bool,
    hook_gateway: ToolHookGateway,
    session_tracker: SessionTracker,
    session_id: str,
    pending_state: PendingState,
    ensure_input_list_fn,
) -> web.StreamResponse:
    """Handle the request that follows a synthetic get_goal call.

    The client has executed get_goal and the result is in the input items.
    MCPTap extracts the result, runs the hook, and either returns the saved
    response (allow) or feeds the block message to the model (block).
    """

    # Extract get_goal result from input items
    get_goal_result = _extract_get_goal_result(working_payload)

    # Update the pending state with the actual result
    pending_state.get_goal_result = get_goal_result

    # Run the hook script
    try:
        decision = await hook_gateway.run_hook(pending_state)
    except RuntimeError as exc:
        LOGGER.error("Tool hook error for session=%s: %s", session_id, exc)
        await hook_gateway.clear_pending(session_id)
        error_resp = _build_hook_error_response(str(exc), pending_state.forced_model)
        if client_wanted_stream:
            sse_raw = _build_sse_from_response(error_resp)
            return await _emit_buffered_response(request, status=200, headers={}, raw=sse_raw)
        else:
            return await _emit_buffered_response(
                request,
                status=200,
                headers={"Content-Type": "application/json"},
                raw=json.dumps(error_resp, ensure_ascii=False).encode("utf-8"),
            )

    action = decision.get("action")

    if action == "allow":
        blocked_files = decision.get("blocked_files", [])
        updated_tool_calls = decision.get("updated_tool_calls", [])
        LOGGER.info(
            "Tool hook allowed tool calls for session=%s (blocked_files=%d, updated_tool_calls=%d)",
            session_id,
            len(blocked_files),
            len(updated_tool_calls),
        )
        await hook_gateway.clear_pending(session_id)

        if blocked_files:
            _write_blocklist(session_id, blocked_files)

        response_raw = pending_state.saved_raw
        if updated_tool_calls:
            modified = _apply_tool_call_updates(pending_state.saved_body_json, updated_tool_calls)
            if modified:
                response_raw = _re_serialize_response(pending_state.saved_body_json, client_wanted_stream)
                LOGGER.info(
                    "Tool hook applied %d tool call updates for session=%s",
                    len(updated_tool_calls),
                    session_id,
                )

        return await _emit_buffered_response(
            request,
            status=pending_state.saved_status,
            headers=pending_state.saved_headers,
            raw=response_raw,
        )

    # action == "block"
    block_message = decision.get("message", "Tool calls blocked by hook")
    LOGGER.info("Tool hook blocked tool calls for session=%s: %s", session_id, block_message)
    await hook_gateway.clear_pending(session_id)

    # Feed the block message to the model and pass through once without hook.
    # Strip the synthetic get_goal call/result from input, add the model's
    # original function calls back, and append the block message as instructions.
    ensure_input_list_fn(working_payload)

    # Remove synthetic get_goal items from input
    working_payload["input"] = _strip_synthetic_get_goal(working_payload["input"])

    # Add the original model output items (function calls) back
    for out_item in pending_state.saved_body_json.get("output") or []:
        if isinstance(out_item, dict):
            working_payload["input"].append(out_item)

    # Add the block message as a system instruction
    existing_instructions = working_payload.get("instructions", "") or ""
    block_instruction = f"\n\n[TOOL CALL BLOCKED] {block_message}"
    working_payload["instructions"] = existing_instructions + block_instruction

    # Make one upstream request with pass-through (no hook)
    working_payload.pop("previous_response_id", None)

    try:
        status, resp_headers, raw, body_json = await _post_upstream_buffered(
            session,
            path,
            request_headers,
            working_payload,
            stream=client_wanted_stream,
        )
    except (ClientError, OSError) as exc:
        LOGGER.exception("Upstream request failed after block: %s", exc)
        return web.json_response(
            {
                "error": {
                    "message": "OpenRouter proxy could not reach the upstream API",
                    "type": "proxy_upstream_error",
                }
            },
            status=502,
        )

    # Accumulate tokens from the post-block response
    if body_json:
        tokens = _extract_usage_total_tokens(body_json)
        if tokens > 0:
            await session_tracker.add_usage(session_id, tokens)

    # Pass through this response without running the hook again
    return await _emit_buffered_response(
        request,
        status=status,
        headers=resp_headers,
        raw=raw,
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
    except Exception as exc:
        LOGGER.exception("Failed to start MCP intercept; continuing without interception: %s", exc)


async def stop_mcp_intercept(app: web.Application) -> None:
    intercept: MCPInterceptor = app.get("mcp_intercept")
    if intercept is not None:
        await intercept.stop()


def build_app() -> web.Application:
    app = web.Application(client_max_size=100 * 1024 * 1024)
    try:
        intercept_config = _load_intercept_config()
    except Exception as exc:
        LOGGER.exception("Invalid MCP_TAP_INTERCEPT_YAML; disabling intercept (%s)", exc)
        intercept_config = None

    try:
        per_model_config = _load_per_model_config()
    except Exception as exc:
        LOGGER.exception("Invalid MCP_TAP_PER_MODEL_YAML; disabling per-model config (%s)", exc)
        per_model_config = None

    session_tracker = SessionTracker()
    hook_gateway = ToolHookGateway(session_tracker)

    if not per_model_config:
        LOGGER.info("Per-model config disabled (MCP_TAP_PER_MODEL_YAML is empty)")
    if not hook_gateway.enabled:
        LOGGER.info("Tool hook disabled (MCP_TAP_USE_TOOL_HOOK is empty)")

    app["mcp_intercept"] = MCPInterceptor(intercept_config)
    app["per_model_config"] = per_model_config
    app["session_tracker"] = session_tracker
    app["hook_gateway"] = hook_gateway
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
