"""MCP tool interception — exposes MCP tools to the model and executes them locally.

Owns a single long-lived MCP subprocess and dispatches tool calls to it.
"""

import asyncio
import contextlib
import json
import os
from typing import Any, Dict, List, Optional, Set

import yaml  # type: ignore

from mcptap.settings import LOGGER, settings


class InterceptedTool:
    """One tool exposed to the model that is backed by an MCP tool call."""

    def __init__(self, mapping: Dict[str, Any]) -> None:
        self.expose_as: str = mapping["expose_as"]
        self.mcp_tool: str = mapping["mcp_tool"]
        override = mapping.get("override") or {}
        if not isinstance(override, dict):
            raise RuntimeError(f"MCP intercept mapping {self.expose_as!r} has non-object 'override'")
        self.override: Dict[str, Any] = override

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
        self._lock: Optional[Any] = None  # asyncio.Lock, created on start
        self._started = False

    @property
    def enabled(self) -> bool:
        return bool(self.tools)

    def tool_names(self) -> Set[str]:
        return {tool.expose_as for tool in self.tools}

    def _find_tool(self, exposed_name: str) -> Optional[InterceptedTool]:
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
                server["mcp_command"],
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
        tool = self._find_tool(exposed_name)
        if tool is None:
            raise KeyError(exposed_name)
        if self._session is None or self._lock is None:
            raise RuntimeError("MCP session is not started")
        async with self._lock:
            LOGGER.info("MCP call: expose_as=%r mcp_tool=%r", exposed_name, tool.mcp_tool)
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(tool.mcp_tool, arguments or {}),
                    timeout=settings.intercept_tool_timeout,
                )
            except asyncio.TimeoutError:
                LOGGER.warning(
                    "MCP call timed out after %.1fs: %r",
                    settings.intercept_tool_timeout,
                    exposed_name,
                )
                return json.dumps({"error": f"MCP tool {tool.mcp_tool} timed out"})
            except Exception as exc:
                LOGGER.exception("MCP call failed: %s", exc)
                return json.dumps({"error": f"MCP tool {tool.mcp_tool} failed: {exc}"})
        return serialize_mcp_result(result)


def serialize_mcp_result(result: Any) -> str:
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


def load_intercept_config() -> Optional[Dict[str, Any]]:
    """Return the intercept config as a validated dict, or None if disabled.

    MCP_TAP_INTERCEPT_YAML is a YAML object describing a single MCP server plus a
    list of mappings. Server fields (mcp_command/mcp_args/mcp_env/mcp_cwd) live
    on the top-level object; each entry in `mappings` carries `expose_as`,
    `mcp_tool`, and optionally `description`/`parameters`.

    Value can be prefixed with `@` to load YAML from a file path.
    """
    if not settings.intercept_yaml:
        return None
    payload = settings.intercept_yaml
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
