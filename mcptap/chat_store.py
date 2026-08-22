"""Persistent Chat history store backed by SQLite with bloat controls."""

from __future__ import annotations

import copy
import gzip
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_TTL_SECONDS = 900  # 15 minutes
DEFAULT_MAX_ROWS = 200
DEFAULT_MAX_BYTES = 20 * 1024 * 1024  # 20 MB compressed
DEFAULT_MAX_ENTRY_BYTES = 512 * 1024  # 512 kB compressed per entry


class PersistentChatStore:
    """SQLite-backed store for Chat conversation histories.

    Each entry is gzip-compressed JSON and guarded by TTL, row count,
    and total byte caps to prevent unbounded growth. The 425k-token
    Codex compaction payloads are compressed and evicted automatically.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_rows: int = DEFAULT_MAX_ROWS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    ) -> None:
        from mcptap.settings import settings

        resolved = Path(db_path) if db_path is not None else Path(settings.log_db_path)
        self._db_path = str(resolved)
        self._ttl = ttl_seconds
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._max_entry_bytes = max_entry_bytes
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------ helpers

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._ensure_table()
        return self._conn

    def _ensure_table(self) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_histories (
                id          TEXT PRIMARY KEY,
                created_at  REAL NOT NULL,
                expires_at  REAL NOT NULL,
                data        BLOB NOT NULL,
                bytes       INTEGER NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_histories_expires ON chat_histories(expires_at)")
        self._conn.commit()

    @staticmethod
    def _compress(messages: List[Dict[str, Any]]) -> bytes:
        raw = json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return gzip.compress(raw)

    @staticmethod
    def _decompress(blob: bytes) -> List[Dict[str, Any]]:
        raw = gzip.decompress(blob).decode("utf-8")
        data = json.loads(raw)
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------ public

    def store(self, response_id: str, messages: List[Dict[str, Any]]) -> None:
        blob = self._compress(messages)
        if len(blob) > self._max_entry_bytes:
            # Drop oversized entries instead of storing a 425k-token compaction blob.
            return
        now = time.time()
        expires = now + self._ttl
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO chat_histories (id, created_at, expires_at, data, bytes) VALUES (?, ?, ?, ?, ?)",
            (response_id, now, expires, blob, len(blob)),
        )
        conn.commit()
        self._prune()

    def get(self, response_id: str) -> Optional[List[Dict[str, Any]]]:
        conn = self._connect()
        row = conn.execute("SELECT data, expires_at FROM chat_histories WHERE id = ?", (response_id,)).fetchone()
        if row is None:
            return None
        blob, expires_at = row
        if expires_at < time.time():
            conn.execute("DELETE FROM chat_histories WHERE id = ?", (response_id,))
            conn.commit()
            return None
        try:
            return self._decompress(blob)
        except Exception:
            conn.execute("DELETE FROM chat_histories WHERE id = ?", (response_id,))
            conn.commit()
            return None

    def store_response(self, response_id: str, messages: List[Dict[str, Any]], body: Dict[str, Any]) -> None:
        history = copy.deepcopy(messages)
        choices = body.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                history.append(copy.deepcopy(message))
        else:
            # Fallback: convert Responses body shape
            from mcptap.chat_completions import responses_body_to_chat_messages

            history.extend(responses_body_to_chat_messages(body))
        self.store(response_id, history)

    def delete(self, response_id: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM chat_histories WHERE id = ?", (response_id,))
        conn.commit()

    def count(self) -> int:
        conn = self._connect()
        row = conn.execute("SELECT COUNT(*) FROM chat_histories").fetchone()
        return int(row[0]) if row else 0

    def total_bytes(self) -> int:
        conn = self._connect()
        row = conn.execute("SELECT COALESCE(SUM(bytes),0) FROM chat_histories").fetchone()
        return int(row[0]) if row else 0

    def purge_expired(self) -> int:
        conn = self._connect()
        cur = conn.execute("DELETE FROM chat_histories WHERE expires_at < ?", (time.time(),))
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return cur.rowcount

    def _prune(self) -> None:
        conn = self._connect()
        # Expired first
        conn.execute("DELETE FROM chat_histories WHERE expires_at < ?", (time.time(),))
        # Row count cap (LRU by created_at)
        row = conn.execute("SELECT COUNT(*) FROM chat_histories").fetchone()
        count = int(row[0]) if row else 0
        if count > self._max_rows:
            to_delete = count - self._max_rows
            conn.execute(
                "DELETE FROM chat_histories WHERE id IN "
                "(SELECT id FROM chat_histories ORDER BY created_at ASC LIMIT ?)",
                (to_delete,),
            )
        # Total bytes cap (LRU)
        row = conn.execute("SELECT COALESCE(SUM(bytes),0) FROM chat_histories").fetchone()
        total = int(row[0]) if row else 0
        while total > self._max_bytes:
            oldest = conn.execute("SELECT id, bytes FROM chat_histories ORDER BY created_at ASC LIMIT 1").fetchone()
            if oldest is None:
                break
            oid, ob = oldest
            conn.execute("DELETE FROM chat_histories WHERE id = ?", (oid,))
            total -= int(ob)
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
