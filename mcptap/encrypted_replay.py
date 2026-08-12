"""Helpers for model-bound encrypted Responses API replay items."""

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Set

_ENCRYPTED_REPLAY_ITEM_TYPES = {"reasoning", "compaction"}


@dataclass(frozen=True)
class ReplayItemRemoval:
    """Counts encrypted replay items removed from a request."""

    reasoning: int = 0
    compaction: int = 0

    @property
    def total(self) -> int:
        """Return the total number of removed replay items."""
        return self.reasoning + self.compaction


@dataclass(frozen=True)
class EncryptedReplayPlan:
    """Describes how an encrypted replay request was prepared."""

    previous_route_fingerprint: Optional[str]
    retry_on_404: bool
    removal: ReplayItemRemoval


@dataclass(frozen=True)
class ReplayContext:
    """Identifies encrypted replay handling for one Responses API request."""

    session_id: str
    route_fingerprint: str
    previous_route_fingerprint: Optional[str]
    retry_on_404: bool


def build_route_fingerprint(
    *,
    path: str,
    model: str,
    upstream_base_url: str,
    upstream_provider: str,
    api_key: str,
    provider: Any,
) -> str:
    """Build a non-reversible identifier for the effective upstream route."""
    route = {
        "api_key_hash": _sha256(api_key),
        "model": model,
        "path": path,
        "provider": provider,
        "upstream_base_url": upstream_base_url.rstrip("/"),
        "upstream_provider": upstream_provider,
    }
    encoded_route = json.dumps(route, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return _sha256(encoded_route)


def encrypted_replay_hashes(items: Any) -> Set[str]:
    """Return hashes of encrypted reasoning and compaction items in ``items``."""
    if not isinstance(items, list):
        return set()
    return {item_hash for item in items if (item_hash := encrypted_replay_item_hash(item)) is not None}


def encrypted_replay_item_hash(item: Any) -> Optional[str]:
    """Return a stable hash for one encrypted replay item."""
    if not _is_encrypted_replay_item(item):
        return None
    encrypted_content = item["encrypted_content"]
    return _sha256(f"{item['type']}:{encrypted_content}")


def encrypted_replay_hashes_from_payload(payload: Dict[str, Any]) -> Set[str]:
    """Return hashes of encrypted replay items in a Responses request payload."""
    return encrypted_replay_hashes(payload.get("input"))


def encrypted_replay_hashes_from_response(response_body: Optional[Dict[str, Any]]) -> Set[str]:
    """Return hashes of encrypted replay items in a Responses response body."""
    if not response_body:
        return set()
    return encrypted_replay_hashes(response_body.get("output"))


def filter_encrypted_replay_items(
    payload: Dict[str, Any],
    allowed_hashes: Iterable[str],
) -> ReplayItemRemoval:
    """Remove encrypted replay items that are not valid for the current route."""
    input_items = payload.get("input")
    if not isinstance(input_items, list):
        return ReplayItemRemoval()

    allowed = set(allowed_hashes)
    filtered_items = []
    removed_reasoning = 0
    removed_compaction = 0

    for item in input_items:
        item_hash = encrypted_replay_item_hash(item)
        if item_hash is None or item_hash in allowed:
            filtered_items.append(item)
            continue

        if item["type"] == "reasoning":
            removed_reasoning += 1
        else:
            removed_compaction += 1

    if removed_reasoning or removed_compaction:
        payload["input"] = filtered_items

    return ReplayItemRemoval(
        reasoning=removed_reasoning,
        compaction=removed_compaction,
    )


def has_encrypted_replay_items(payload: Dict[str, Any]) -> bool:
    """Return whether a Responses request contains encrypted replay items."""
    return bool(encrypted_replay_hashes_from_payload(payload))


def is_encrypted_replay_error(status: int, response_body: Optional[Dict[str, Any]]) -> bool:
    """Return whether an upstream response rejects model-bound encrypted replay."""
    if status != 404 or not isinstance(response_body, dict):
        return False
    error = response_body.get("error")
    if not isinstance(error, dict):
        return False
    message = error.get("message")
    if not isinstance(message, str):
        return False
    return "encrypted payloads can only be replayed" in message.lower()


def log_replay_sanitization(
    logger: logging.Logger,
    *,
    session_id: str,
    removal: ReplayItemRemoval,
    previous_route_fingerprint: Optional[str],
    route_fingerprint: str,
    reason: str,
) -> None:
    """Emit DEBUG-only encrypted replay diagnostics without payload content."""
    if not removal.total:
        return
    logger.debug(
        "Encrypted replay sanitized: session_id=%s removed_reasoning=%d removed_compaction=%d "
        "old_route_hash=%s new_route_hash=%s reason=%s",
        session_id,
        removal.reasoning,
        removal.compaction,
        previous_route_fingerprint or "unknown",
        route_fingerprint,
        reason,
    )


def _is_encrypted_replay_item(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.get("type") in _ENCRYPTED_REPLAY_ITEM_TYPES
        and isinstance(item.get("encrypted_content"), str)
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
