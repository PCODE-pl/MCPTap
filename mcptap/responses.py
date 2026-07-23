"""Response inspection and manipulation helpers for the OpenAI Responses API.

Pure functions for extracting, building, and modifying response bodies,
function calls, and SSE streams. No dependency on aiohttp or application state.
"""

import json
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from mcptap.settings import SYNTHETIC_GET_GOAL_CALL_ID, SYNTHETIC_GET_GOAL_TOOL_NAME


def iter_function_calls(response_body: Dict[str, Any]) -> Iterator[Tuple[Dict[str, Any], str, str, str]]:
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
            item.get("call_id") or "",
            item.get("name") or "",
            item.get("arguments") or "{}",
        )


def extract_usage_total_tokens(body_json: Optional[Dict[str, Any]]) -> int:
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


def extract_client_tool_calls(
    body_json: Dict[str, Any],
    intercept_names: Set[str],
) -> List[Dict[str, Any]]:
    """Extract function_call items that are NOT intercepted MCP tools.

    Returns a list of dicts with keys: call_id, name, arguments.
    """
    result = []
    for _, call_id, name, arguments in iter_function_calls(body_json):
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
        result.append({"call_id": call_id, "name": name, "arguments": parsed_args})
    return result


def has_intercepted_calls(body_json: Dict[str, Any], intercept_names: Set[str]) -> bool:
    """Check if the response contains any intercepted MCP tool calls."""
    for _, call_id, name, _ in iter_function_calls(body_json):
        if name in intercept_names and call_id:
            return True
    return False


def has_client_tool_calls(body_json: Dict[str, Any], intercept_names: Set[str]) -> bool:
    """Check if the response contains any client (non-intercepted) function calls."""
    for _, call_id, name, _ in iter_function_calls(body_json):
        if name not in intercept_names and call_id:
            return True
    return False


def extract_get_goal_result(working_payload: Dict[str, Any]) -> Dict[str, Any]:
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


def strip_synthetic_get_goal(input_items: List[Any]) -> List[Any]:
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


def build_synthetic_tool_response(forced_model: str, tool_name: str) -> Dict[str, Any]:
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


def build_synthetic_get_goal_response(forced_model: str) -> Dict[str, Any]:
    """Build a synthetic response containing a single get_goal function_call."""
    return build_synthetic_tool_response(forced_model, SYNTHETIC_GET_GOAL_TOOL_NAME)


def build_hook_error_response(error_message: str, forced_model: str) -> Dict[str, Any]:
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


def build_sse_from_response(response: Dict[str, Any]) -> bytes:
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


def response_json_from_sse(raw: bytes) -> Optional[Dict[str, Any]]:
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


def extract_intercepted_calls(
    response_body: Dict[str, Any],
    intercept_tool_names: Set[str],
) -> List[Tuple[Dict[str, Any], str, str, str]]:
    hits = []
    for item, call_id, name, arguments in iter_function_calls(response_body):
        if name in intercept_tool_names and call_id:
            hits.append((item, call_id, name, arguments))
    return hits


def apply_tool_call_updates(
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


def re_serialize_response(
    saved_body_json: Dict[str, Any],
    client_wanted_stream: bool,
) -> bytes:
    """Re-serialize a response body to bytes for the client.

    For non-stream responses this produces a JSON byte string.  For stream
    responses this builds a minimal SSE byte stream via
    ``build_sse_from_response``.
    """
    if client_wanted_stream:
        return build_sse_from_response(saved_body_json)
    return json.dumps(saved_body_json, ensure_ascii=False).encode("utf-8")
