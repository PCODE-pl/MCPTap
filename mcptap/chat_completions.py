"""Conversion between OpenAI Responses and Chat Completions payloads."""

import copy
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple


class ChatConversationStore:
    """In-memory Responses response ID to Chat message history mapping.

    For persistence across restarts see ``mcptap.chat_store.PersistentChatStore``.
    """

    def __init__(self) -> None:
        self._histories: Dict[str, List[Dict[str, Any]]] = {}

    def store(self, response_id: str, messages: List[Dict[str, Any]]) -> None:
        self._histories[response_id] = copy.deepcopy(messages)

    def get(self, response_id: str) -> Optional[List[Dict[str, Any]]]:
        messages = self._histories.get(response_id)
        return copy.deepcopy(messages) if messages is not None else None

    def store_response(self, response_id: str, messages: List[Dict[str, Any]], body: Dict[str, Any]) -> None:
        history = copy.deepcopy(messages)
        choices = body.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                history.append(copy.deepcopy(message))
        else:
            history.extend(responses_body_to_chat_messages(body))
        self.store(response_id, history)


_REQUEST_FIELDS = {
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "response_format",
    "tool_choice",
    "parallel_tool_calls",
    "stream",
    "seed",
    "user",
}


def _normalize_chat_role(role: Any) -> Any:
    return "system" if role == "developer" else role


def _has_chat_content(content: Any) -> bool:
    if content is None or content == "" or content == []:
        return False
    if isinstance(content, list):
        return any(isinstance(part, dict) and part for part in content)
    return True


def _normalize_chat_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    normalized = copy.deepcopy(message)
    normalized["role"] = _normalize_chat_role(message.get("role"))
    tool_calls = normalized.get("tool_calls")
    has_tool_calls = isinstance(tool_calls, list) and bool(tool_calls)
    if normalized["role"] == "assistant" and not _has_chat_content(normalized.get("content")):
        if not has_tool_calls:
            return None
        normalized["content"] = None
    return normalized


def responses_request_to_chat(
    payload: Dict[str, Any],
    conversation_store: Optional[Any] = None,
    stream: Optional[bool] = None,
) -> Dict[str, Any]:
    """Convert a Responses request into an OpenAI Chat Completions request."""
    result: Dict[str, Any] = {"model": payload.get("model", "")}
    previous_id = payload.get("previous_response_id")
    messages: List[Dict[str, Any]] = []
    if previous_id and conversation_store is not None:
        messages = [
            normalized
            for message in conversation_store.get(str(previous_id)) or []
            if isinstance(message, dict)
            for normalized in [_normalize_chat_message(message)]
            if normalized is not None
        ]

    instructions = payload.get("instructions")
    if (
        isinstance(instructions, str)
        and instructions
        and not any(
            message.get("role") in {"system", "developer"} and message.get("content") == instructions
            for message in messages
        )
    ):
        messages.append({"role": "system", "content": instructions})

    messages.extend(_input_to_messages(payload.get("input")))
    result["messages"] = messages

    effective_stream = stream if stream is not None else bool(payload.get("stream"))

    for key in _REQUEST_FIELDS:
        if key in payload and key != "stream":
            result[key] = copy.deepcopy(payload[key])

    if effective_stream:
        result["stream"] = True
        result["stream_options"] = {"include_usage": True}
    elif "stream" in payload:
        result["stream"] = copy.deepcopy(payload["stream"])

    if "max_output_tokens" in payload:
        result["max_tokens"] = payload["max_output_tokens"]
    elif "max_tokens" in payload:
        result["max_tokens"] = payload["max_tokens"]

    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("effort"), str):
        result["reasoning_effort"] = reasoning["effort"]

    text = payload.get("text")
    if "response_format" not in result and isinstance(text, dict):
        text_format = text.get("format")
        if isinstance(text_format, dict):
            result["response_format"] = _responses_text_format_to_chat(text_format)

    tools = payload.get("tools")
    if isinstance(tools, list):
        result["tools"] = [_response_tool_to_chat(tool) for tool in tools if _is_function_tool(tool)]

    return result


def chat_response_to_responses(
    body: Dict[str, Any],
    response_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert one Chat Completions JSON response to a Responses response."""
    if isinstance(body.get("error"), dict):
        return copy.deepcopy(body)

    choices = body.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    message = message if isinstance(message, dict) else {}
    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    output: List[Dict[str, Any]] = []

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            call_id = str(tool_call.get("id") or f"call_{uuid.uuid4().hex[:24]}")
            item: Dict[str, Any] = {
                "type": "function_call",
                "id": call_id,
                "call_id": call_id,
                "name": function.get("name") or "",
                "arguments": function.get("arguments") or "{}",
            }
            if isinstance(message.get("reasoning_content"), str):
                item["reasoning_content"] = message["reasoning_content"]
            output.append(item)

    content = message.get("content")
    if content is not None and not isinstance(content, list):
        output.append(_chat_message_to_response_item(message, str(content)))
    elif isinstance(content, list):
        text = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if text:
            output.append(_chat_message_to_response_item(message, text))

    response_id = response_id or f"resp_{uuid.uuid4().hex[:24]}"
    usage = _chat_usage_to_responses(body.get("usage"))
    status = "incomplete" if finish_reason in {"tool_calls", "length", "content_filter"} else "completed"
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "model": body.get("model", ""),
        "status": status,
        "output": output,
        "usage": usage,
    }


def _build_chat_sse_from_response(response: Dict[str, Any]) -> bytes:
    """Build Responses SSE with content and tool-call delta events."""
    lines: List[str] = []
    sequence_number = 0

    def append_event(event_type: str, **payload: Any) -> None:
        nonlocal sequence_number
        event = {"type": event_type, "sequence_number": sequence_number, **payload}
        sequence_number += 1
        data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        lines.extend([f"data: {data}", ""])

    in_progress = copy.deepcopy(response)
    in_progress["status"] = "in_progress"
    in_progress["output"] = []
    in_progress["usage"] = None
    append_event("response.created", response=in_progress)
    append_event("response.in_progress", response=in_progress)

    for output_index, item in enumerate(response.get("output") or []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", ""))
        added_item = copy.deepcopy(item)
        added_item["status"] = "in_progress"
        if item.get("type") == "message":
            added_item["content"] = []
            added_item["phase"] = "commentary"
        elif item.get("type") == "function_call":
            added_item["arguments"] = ""
        append_event("response.output_item.added", output_index=output_index, item=added_item)

        if item.get("type") == "message":
            content = item.get("content") or []
            text = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
            part = {"type": "output_text", "text": "", "annotations": [], "logprobs": []}
            append_event(
                "response.content_part.added",
                item_id=item_id,
                output_index=output_index,
                content_index=0,
                part=part,
            )
            if text:
                append_event(
                    "response.output_text.delta",
                    item_id=item_id,
                    output_index=output_index,
                    content_index=0,
                    delta=text,
                    logprobs=[],
                )
            append_event(
                "response.output_text.done",
                item_id=item_id,
                output_index=output_index,
                content_index=0,
                text=text,
                logprobs=[],
            )
            append_event(
                "response.content_part.done",
                item_id=item_id,
                output_index=output_index,
                content_index=0,
                part={"type": "output_text", "text": text, "annotations": [], "logprobs": []},
            )
        elif item.get("type") == "function_call":
            arguments = str(item.get("arguments", ""))
            if arguments:
                append_event(
                    "response.function_call_arguments.delta",
                    item_id=item_id,
                    output_index=output_index,
                    delta=arguments,
                )
            append_event(
                "response.function_call_arguments.done",
                item_id=item_id,
                output_index=output_index,
                arguments=arguments,
            )

        append_event("response.output_item.done", output_index=output_index, item=item)

    append_event("response.completed", response=response)
    lines.extend(["data: [DONE]", ""])
    return "\n".join(lines).encode("utf-8")


def chat_sse_to_responses(raw: bytes) -> Dict[str, Any]:
    """Aggregate Chat Completions SSE and return Responses JSON plus SSE bytes."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    if text.lstrip().startswith("{"):
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return {"error": {"message": "Invalid upstream Chat Completions response", "type": "proxy_upstream_error"}}
        if not isinstance(body, dict):
            return {}
        if isinstance(body.get("error"), dict):
            return body
        result = chat_response_to_responses(body)
        result["sse"] = _build_chat_sse_from_response(result)
        return result

    aggregate: Dict[str, Any] = {"id": "", "model": "", "choices": [], "usage": None}
    choices: Dict[int, Dict[str, Any]] = {}
    tool_calls: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        aggregate["id"] = chunk.get("id") or aggregate["id"]
        aggregate["model"] = chunk.get("model") or aggregate["model"]
        if isinstance(chunk.get("usage"), dict):
            aggregate["usage"] = chunk["usage"]
        raw_choices = chunk.get("choices")
        if not isinstance(raw_choices, list):
            continue
        for raw_choice in raw_choices:
            if not isinstance(raw_choice, dict):
                continue
            index = int(raw_choice.get("index", 0))
            state = choices.setdefault(index, {"message": {"role": "assistant", "content": ""}, "finish_reason": None})
            delta = raw_choice.get("delta")
            if isinstance(delta, dict):
                if isinstance(delta.get("role"), str):
                    state["message"]["role"] = delta["role"]
                if isinstance(delta.get("content"), str):
                    state["message"]["content"] += delta["content"]
                if isinstance(delta.get("reasoning_content"), str):
                    state["message"]["reasoning_content"] = (
                        state["message"].get("reasoning_content", "") + delta["reasoning_content"]
                    )
                raw_tool_calls = delta.get("tool_calls")
                if isinstance(raw_tool_calls, list):
                    for raw_tool in raw_tool_calls:
                        if not isinstance(raw_tool, dict):
                            continue
                        tool_index = int(raw_tool.get("index", 0))
                        key = (index, tool_index)
                        state_tool = tool_calls.setdefault(
                            key, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        )
                        if isinstance(raw_tool.get("id"), str):
                            state_tool["id"] = raw_tool["id"]
                        if isinstance(raw_tool.get("type"), str):
                            state_tool["type"] = raw_tool["type"]
                        function = raw_tool.get("function")
                        if isinstance(function, dict):
                            if isinstance(function.get("name"), str):
                                state_tool["function"]["name"] += function["name"]
                            if isinstance(function.get("arguments"), str):
                                state_tool["function"]["arguments"] += function["arguments"]
            if raw_choice.get("finish_reason") is not None:
                state["finish_reason"] = raw_choice["finish_reason"]

    for index, state in choices.items():
        state["message"]["content"] = state["message"]["content"] or None
        state_tools = [tool for (choice_index, _), tool in sorted(tool_calls.items()) if choice_index == index]
        if state_tools:
            state["message"]["tool_calls"] = state_tools
            state["message"]["content"] = None
        aggregate["choices"].append({"index": index, **state})
    aggregate["choices"].sort(key=lambda choice: choice["index"])
    result = chat_response_to_responses(aggregate)
    result["sse"] = _build_chat_sse_from_response(result)
    return result


def responses_body_to_chat_messages(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert a canonical Responses body back into Chat history messages."""
    messages: List[Dict[str, Any]] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            if not messages or messages[-1].get("role") != "assistant" or "tool_calls" not in messages[-1]:
                messages.append({"role": "assistant", "content": None, "tool_calls": []})
            tool_call = {
                "id": item.get("call_id") or item.get("id", ""),
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", "{}"),
                },
            }
            messages[-1]["tool_calls"].append(tool_call)
            if isinstance(item.get("reasoning_content"), str):
                messages[-1]["reasoning_content"] = item["reasoning_content"]
        elif item.get("type") == "message":
            content = item.get("content") or []
            text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            message = {"role": item.get("role", "assistant"), "content": text}
            if isinstance(item.get("reasoning_content"), str):
                message["reasoning_content"] = item["reasoning_content"]
            messages.append(message)
    return messages


def convert_chat_response(
    raw: bytes, stream: bool, response_id: Optional[str] = None
) -> Tuple[bytes, Optional[Dict[str, Any]]]:
    """Convert Chat response bytes to client-facing Responses bytes and JSON."""
    if stream:
        result = chat_sse_to_responses(raw)
        if "error" in result:
            return raw, result
        result = {key: value for key, value in result.items() if key != "sse"}
        if response_id:
            result["id"] = response_id
        return _build_chat_sse_from_response(result), {key: value for key, value in result.items() if key != "sse"}
    body = _parse_json(raw)
    if body is None or "error" in body:
        return raw, body
    result = chat_response_to_responses(body, response_id=response_id)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), result


def _input_to_messages(input_value: Any) -> List[Dict[str, Any]]:
    if isinstance(input_value, str):
        return [{"role": "user", "content": input_value}]
    if not isinstance(input_value, list):
        return []

    messages: List[Dict[str, Any]] = []
    pending_calls: Optional[Dict[str, Any]] = None
    for item in input_value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "function_call":
            if pending_calls is None:
                pending_calls = {"role": "assistant", "content": None, "tool_calls": []}
                messages.append(pending_calls)
            if isinstance(item.get("reasoning_content"), str):
                pending_calls["reasoning_content"] = item["reasoning_content"]
            pending_calls["tool_calls"].append(
                {
                    "id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {"name": item.get("name", ""), "arguments": item.get("arguments", "{}")},
                }
            )
            continue
        pending_calls = None
        if item_type == "function_call_output":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id", ""),
                    "content": _tool_output_to_text(item.get("output")),
                }
            )
            continue
        if item_type in {"reasoning", "compaction"}:
            continue
        role = item.get("role")
        if role in {"system", "developer", "user", "assistant", "tool"}:
            message = {
                "role": _normalize_chat_role(role),
                "content": _content_to_chat(item.get("content")),
            }
            if isinstance(item.get("tool_calls"), list) and item["tool_calls"]:
                message["tool_calls"] = copy.deepcopy(item["tool_calls"])
            normalized = _normalize_chat_message(message)
            if normalized is not None:
                messages.append(normalized)
            continue
        if item_type == "message":
            message = {
                "role": _normalize_chat_role(item.get("role", "user")),
                "content": _content_to_chat(item.get("content")),
            }
            normalized = _normalize_chat_message(message)
            if normalized is not None:
                messages.append(normalized)
    return messages


def _content_to_chat(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    parts: List[Any] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type in {"input_text", "text", "output_text"}:
            parts.append({"type": "text", "text": part.get("text", "")})
        elif part_type in {"input_image", "image_url"}:
            if part_type == "input_image":
                parts.append({"type": "image_url", "image_url": {"url": part.get("image_url") or part.get("url", "")}})
            else:
                parts.append(copy.deepcopy(part))
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0]["text"]
    return parts


def _tool_output_to_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    return json.dumps(output, ensure_ascii=False)


def _parse_json(raw: bytes) -> Optional[Dict[str, Any]]:
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return result if isinstance(result, dict) else None


def _is_function_tool(tool: Any) -> bool:
    return (
        isinstance(tool, dict)
        and tool.get("type") == "function"
        and (isinstance(tool.get("name"), str) or isinstance(tool.get("function"), dict))
    )


def _response_tool_to_chat(tool: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(tool.get("function"), dict):
        function = copy.deepcopy(tool["function"])
    else:
        function = {
            key: copy.deepcopy(tool[key]) for key in ("name", "description", "parameters", "strict") if key in tool
        }
    return {"type": "function", "function": function}


def _responses_text_format_to_chat(text_format: Dict[str, Any]) -> Dict[str, Any]:
    if text_format.get("type") == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                key: copy.deepcopy(text_format[key])
                for key in ("name", "description", "schema", "strict")
                if key in text_format
            },
        }
    return {"type": text_format.get("type", "text")}


def _chat_message_to_response_item(message: Dict[str, Any], content: str) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "type": "message",
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "status": "completed",
        "role": "assistant",
        "phase": "commentary",
        "content": [{"type": "output_text", "text": content, "annotations": []}],
    }
    if isinstance(message.get("reasoning_content"), str):
        item["reasoning_content"] = message["reasoning_content"]
    return item


def _chat_usage_to_responses(usage: Any) -> Optional[Dict[str, int]]:
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "output_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }
