#!/usr/bin/env python3
"""MCPTap — transparent LLM proxy for the OpenAI Responses API.

This is the entry-point script; all logic lives in the ``mcptap`` package.

to see logs:
    journalctl --user -u mcptap.service -f
for DEBUG level:
    journalctl --user -u mcptap.service -p debug -f
"""

from mcptap.app import build_app, health, main, proxy
from mcptap.file_block import blocklist_file_path, clear_blocklist, write_blocklist
from mcptap.mcp_intercept import (
    InterceptedTool,
    MCPInterceptor,
    load_intercept_config,
    serialize_mcp_result,
)
from mcptap.responses import (
    apply_tool_call_updates,
    build_hook_error_response,
    build_sse_from_response,
    build_synthetic_get_goal_response,
    build_synthetic_tool_response,
    extract_client_tool_calls,
    extract_get_goal_result,
    extract_intercepted_calls,
    extract_usage_total_tokens,
    has_client_tool_calls,
    has_intercepted_calls,
    iter_function_calls,
    re_serialize_response,
    response_json_from_sse,
    strip_synthetic_get_goal,
)
from mcptap.session import SessionTracker, uuid_v7_timestamp
from mcptap.settings import (
    COMMUNICATION_LOGGER,
    DEBUG_PAYLOAD_KEYS,
    HOP_BY_HOP_HEADERS,
    LOGGER,
    PROVIDER_OPENROUTER,
    PROVIDER_REQUESTY,
    SENSITIVE_HEADER_NAMES,
    SYNTHETIC_GET_GOAL_CALL_ID,
    SYNTHETIC_GET_GOAL_TOOL_NAME,
    settings,
)
from mcptap.tool_hook import PendingState, ToolHookGateway
from mcptap.upstream import forward_rewritten, passthrough, post_upstream_buffered

__all__ = [
    "InterceptedTool",
    "MCPInterceptor",
    "PendingState",
    "SessionTracker",
    "ToolHookGateway",
    "build_app",
    "health",
    "main",
    "proxy",
    "settings",
    "LOGGER",
    "COMMUNICATION_LOGGER",
    "PROVIDER_OPENROUTER",
    "PROVIDER_REQUESTY",
    "SYNTHETIC_GET_GOAL_CALL_ID",
    "SYNTHETIC_GET_GOAL_TOOL_NAME",
    "HOP_BY_HOP_HEADERS",
    "SENSITIVE_HEADER_NAMES",
    "DEBUG_PAYLOAD_KEYS",
    "blocklist_file_path",
    "clear_blocklist",
    "write_blocklist",
    "load_intercept_config",
    "serialize_mcp_result",
    "apply_tool_call_updates",
    "build_hook_error_response",
    "build_sse_from_response",
    "build_synthetic_get_goal_response",
    "build_synthetic_tool_response",
    "extract_client_tool_calls",
    "extract_get_goal_result",
    "extract_intercepted_calls",
    "extract_usage_total_tokens",
    "has_client_tool_calls",
    "has_intercepted_calls",
    "iter_function_calls",
    "re_serialize_response",
    "response_json_from_sse",
    "strip_synthetic_get_goal",
    "uuid_v7_timestamp",
    "forward_rewritten",
    "passthrough",
    "post_upstream_buffered",
]

if __name__ == "__main__":
    main()
