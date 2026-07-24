"""SQLite-based request log storage with schema migrations.

Stores per-request metadata (timestamp, model, provider, tokens, cost,
request/response bodies) and provides paginated queries with cursor-based
pagination for the web UI.
"""

import json
import os
import shutil
import sqlite3
import time
import uuid
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

# ---------------------------------------------------------------------------
# Migration definitions
# ---------------------------------------------------------------------------


class Migration(NamedTuple):
    version: int
    description: str
    sql: str


MIGRATIONS: List[Migration] = [
    Migration(
        version=1,
        description="initial schema",
        sql="""
            CREATE TABLE IF NOT EXISTS request_logs (
                id              TEXT PRIMARY KEY,
                timestamp       REAL NOT NULL,
                session_id      TEXT,
                model           TEXT,
                provider        TEXT,
                input_tokens    INTEGER DEFAULT 0,
                output_tokens   INTEGER DEFAULT 0,
                total_tokens    INTEGER DEFAULT 0,
                cost            REAL DEFAULT 0,
                status_code     INTEGER,
                request_body    TEXT,
                response_body   TEXT,
                request_path    TEXT,
                stream          INTEGER DEFAULT 0,
                duration_ms     INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_request_logs_timestamp
                ON request_logs(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_request_logs_session
                ON request_logs(session_id);
            PRAGMA user_version = 1;
        """,
    ),
]


# ---------------------------------------------------------------------------
# Time range presets
# ---------------------------------------------------------------------------

TIME_RANGES: List[Dict[str, Any]] = [
    {"label": "Past 15 minutes", "value": "15m", "seconds": 900},
    {"label": "Past 30 Minutes", "value": "30m", "seconds": 1800},
    {"label": "Past 1 hour", "value": "1h", "seconds": 3600},
    {"label": "Past 3 hours", "value": "3h", "seconds": 10800},
    {"label": "Past 24 hours", "value": "24h", "seconds": 86400},
    {"label": "Past 48 hours", "value": "48h", "seconds": 172800},
    {"label": "Past 1 week", "value": "1w", "seconds": 604800},
]

TIME_RANGE_MAP: Dict[str, int] = {r["value"]: r["seconds"] for r in TIME_RANGES}


# ---------------------------------------------------------------------------
# LogStore
# ---------------------------------------------------------------------------


class LogStore:
    """SQLite-backed storage for proxy request logs."""

    def __init__(self, db_path: str, enabled: bool = True) -> None:
        self._db_path = db_path
        self._enabled = enabled
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def migrate(self) -> int:
        """Run forward-only migrations. Returns the final schema version."""
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)

        if os.path.exists(self._db_path):
            backup_path = self._db_path + ".bak"
            shutil.copy2(self._db_path, backup_path)

        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for migration in MIGRATIONS:
                if migration.version <= current:
                    continue
                conn.executescript(migration.sql)
                conn.commit()
                current = migration.version
            return current
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def connect(self) -> sqlite3.Connection:
        """Open a check-on-write connection for the proxy's async loop."""
        if self._conn is None:
            os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
        return self._conn

    def record(
        self,
        timestamp: float,
        session_id: str,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cost: float,
        status_code: int,
        request_body: Optional[str],
        response_body: Optional[str],
        request_path: str,
        stream: bool,
        duration_ms: int,
    ) -> str:
        """Insert a request log record. Returns the generated ID."""
        if not self._enabled:
            return ""
        log_id = str(uuid.uuid4())
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO request_logs (
                id, timestamp, session_id, model, provider,
                input_tokens, output_tokens, total_tokens, cost,
                status_code, request_body, response_body,
                request_path, stream, duration_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                timestamp,
                session_id,
                model,
                provider,
                input_tokens,
                output_tokens,
                total_tokens,
                cost,
                status_code,
                request_body,
                response_body,
                request_path,
                1 if stream else 0,
                duration_ms,
            ),
        )
        conn.commit()
        return log_id

    def query(
        self,
        range_seconds: Optional[int],
        before: Optional[float] = None,
        limit: int = 50,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Return (rows, has_more) for paginated log display.

        Uses cursor-based pagination: rows older than *before* (a Unix
        timestamp) are returned, newest first.  ``has_more`` indicates
        whether additional pages exist.
        """
        conn = self.connect()
        conditions: List[str] = []
        params: List[Any] = []

        if range_seconds is not None:
            cutoff = time.time() - range_seconds
            conditions.append("timestamp >= ?")
            params.append(cutoff)

        if before is not None:
            conditions.append("timestamp < ?")
            params.append(before)

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = conn.execute(
            f"""
            SELECT id, timestamp, session_id, model, provider,
                   input_tokens, output_tokens, total_tokens, cost,
                   status_code, request_path, stream, duration_ms
            FROM request_logs{where_clause}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            params + [limit + 1],
        ).fetchall()

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        result = []
        for row in rows:
            result.append(
                {
                    "id": row[0],
                    "timestamp": row[1],
                    "session_id": row[2],
                    "model": row[3],
                    "provider": row[4],
                    "input_tokens": row[5],
                    "output_tokens": row[6],
                    "total_tokens": row[7],
                    "cost": row[8],
                    "status_code": row[9],
                    "request_path": row[10],
                    "stream": bool(row[11]),
                    "duration_ms": row[12],
                }
            )
        return result, has_more

    def get_by_id(self, log_id: str) -> Optional[Dict[str, Any]]:
        """Return the full log record including request/response bodies."""
        conn = self.connect()
        row = conn.execute(
            """
            SELECT id, timestamp, session_id, model, provider,
                   input_tokens, output_tokens, total_tokens, cost,
                   status_code, request_body, response_body,
                   request_path, stream, duration_ms
            FROM request_logs
            WHERE id = ?
            """,
            (log_id,),
        ).fetchone()

        if row is None:
            return None

        return {
            "id": row[0],
            "timestamp": row[1],
            "session_id": row[2],
            "model": row[3],
            "provider": row[4],
            "input_tokens": row[5],
            "output_tokens": row[6],
            "total_tokens": row[7],
            "cost": row[8],
            "status_code": row[9],
            "request_body": _safe_json_parse(row[10]),
            "response_body": _safe_json_parse(row[11]),
            "request_path": row[12],
            "stream": bool(row[13]),
            "duration_ms": row[14],
        }

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Convenience helper
# ---------------------------------------------------------------------------


def record_from_response(
    log_store: Optional["LogStore"],
    *,
    request_body: Optional[Dict[str, Any]],
    response_raw: bytes,
    response_body_json: Optional[Dict[str, Any]],
    session_id: str,
    model: str,
    provider: str,
    status_code: int,
    request_path: str,
    stream: bool,
    start_time: float,
) -> None:
    """Extract usage from a parsed response body and record a log entry.

    This is a convenience wrapper that calls extract_usage_details and
    LogStore.record in one step.  Safe to call when *log_store* is None
    or disabled.
    """
    if log_store is None or not log_store.enabled:
        return
    from mcptap.responses import extract_usage_details

    usage = extract_usage_details(response_body_json)
    log_store.record(
        timestamp=start_time,
        session_id=session_id,
        model=model,
        provider=provider,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        total_tokens=usage["total_tokens"],
        cost=usage["cost"],
        status_code=status_code,
        request_body=json.dumps(request_body, ensure_ascii=False) if request_body else None,
        response_body=response_raw.decode("utf-8", errors="replace") if response_raw else None,
        request_path=request_path,
        stream=stream,
        duration_ms=int((time.time() - start_time) * 1000),
    )


def _safe_json_parse(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
