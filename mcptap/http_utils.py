"""HTTP header filtering, logging, and path mapping utilities."""

import json
import sys
from typing import Any, Dict, Optional

from mcptap.settings import (
    COMMUNICATION_LOGGER,
    HOP_BY_HOP_HEADERS,
    SENSITIVE_HEADER_NAMES,
    settings,
)


def filtered_headers(headers) -> Dict[str, str]:
    return {name: value for name, value in headers.items() if name.lower() not in HOP_BY_HOP_HEADERS}


def _redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    if settings.log_fileredact_headers:
        return {
            name: "<redacted>" if name.lower() in SENSITIVE_HEADER_NAMES else value for name, value in headers.items()
        }
    return headers


def _body_to_log_text(body: Optional[bytes], direction: str) -> str:
    if not body:
        return ""

    if direction == "upstream_request":
        if settings.log_payload_keys:
            body_json = json.loads(body.decode("utf-8"))
            body_json = {k: v for k, v in body_json.items() if k in settings.log_payload_keys}
            return json.dumps(body_json, ensure_ascii=False, sort_keys=True)

        body_json = json.loads(body.decode("utf-8"))
        return json.dumps(list(body_json.keys()), ensure_ascii=False, sort_keys=True)

    return body.decode("utf-8", errors="replace")


def log_communication(
    direction: str,
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[bytes] = None,
    status: Optional[int] = None,
) -> None:
    if not settings.log_file:
        return

    metadata: Dict[str, Any] = {
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
    """Map local /v1/... or /api/v1/... paths onto the upstream base URL."""
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


def deep_getsizeof(obj, seen=None) -> int:
    """Recursively measure the memory footprint of an object."""
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
