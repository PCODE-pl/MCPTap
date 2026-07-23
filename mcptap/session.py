"""Per-session tracking of token usage and elapsed time."""

import asyncio
import time
import uuid
from typing import Any, Dict, Optional

from aiohttp import web  # type: ignore

from mcptap.settings import settings


def uuid_v7_timestamp(uuid_str: str) -> Optional[float]:
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
                ts = uuid_v7_timestamp(session_id)
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
                ts = uuid_v7_timestamp(session_id)
                start_time = ts if ts is not None else time.time()
                info = {
                    "session_id": session_id,
                    "start_time": start_time,
                    "total_tokens": 0,
                    "forced_model": settings.model,
                }
                self._sessions[session_id] = info
            info["total_tokens"] += total_tokens

    async def get_usage(self, session_id: str) -> int:
        async with self._lock:
            info = self._sessions.get(session_id)
            return info["total_tokens"] if info else 0

    async def _get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
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
