"""Tool-call hook gateway — manages pending tool-call batches and runs the hook script.

When the model returns client function calls (non-intercepted), the gateway:
1. Saves the upstream response.
2. Returns a synthetic ``get_goal`` call to the client.
3. On the next request (which contains the get_goal result), runs the hook.
4. Returns the saved response if allowed, or feeds the block message to the model.
"""

import asyncio
import json
import sys
import time
from typing import Any, Dict, List, Optional

from mcptap.session import SessionTracker
from mcptap.settings import settings


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

    def is_expired(self, ttl: Optional[float] = None) -> bool:
        if ttl is None:
            ttl = settings.use_tool_hook_pending_ttl
        return time.time() - self.created_at > ttl


class ToolHookGateway:
    """Manages pending tool-call batches and runs the hook script."""

    def __init__(self, session_tracker: SessionTracker) -> None:
        self.enabled = bool(settings.use_tool_hook)
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
                sid for sid, s in self._pending.items() if now - s.created_at > settings.use_tool_hook_pending_ttl
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
                settings.use_tool_hook,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RuntimeError(f"Failed to start hook script: {exc}") from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(stdin_data.encode("utf-8")),
                timeout=settings.use_tool_hook_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"Hook script timed out after {settings.use_tool_hook_timeout}s")

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
