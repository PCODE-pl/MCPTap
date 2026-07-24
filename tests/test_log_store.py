"""Tests for the SQLite-based request log store."""

import os
import sys
import time

import pytest  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcptap.log_store import TIME_RANGE_MAP, LogStore, record_from_response


@pytest.fixture
def log_store(tmp_path):
    db_path = str(tmp_path / "test_logs.db")
    store = LogStore(db_path)
    store.migrate()
    store.connect()
    yield store
    store.close()


class TestMigrations:
    def test_fresh_db_creates_schema(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        store = LogStore(db_path)
        version = store.migrate()
        assert version == 1
        store.connect()
        tables = store.connect().execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert ("request_logs",) in tables
        store.close()

    def test_migrate_is_idempotent(self, log_store):
        version = log_store.migrate()
        assert version == 1

    def test_migrate_creates_backup_on_existing_db(self, tmp_path):
        db_path = str(tmp_path / "with_backup.db")
        store = LogStore(db_path)
        store.migrate()
        store.connect()
        store.record(
            timestamp=time.time(),
            session_id="s1",
            model="m1",
            provider="p1",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            cost=0.01,
            status_code=200,
            request_body="{}",
            response_body="{}",
            request_path="/v1/responses",
            stream=False,
            duration_ms=100,
        )
        store.close()
        # Second migration on existing DB should create a backup
        store2 = LogStore(db_path)
        store2.migrate()
        assert os.path.exists(db_path + ".bak")
        store2.close()


class TestRecord:
    def test_record_inserts_row(self, log_store):
        log_id = log_store.record(
            timestamp=time.time(),
            session_id="session1",
            model="claude-4",
            provider="anthropic",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost=0.02,
            status_code=200,
            request_body='{"model":"claude-4"}',
            response_body='{"id":"resp_1","usage":{"total_tokens":150}}',
            request_path="/v1/responses",
            stream=False,
            duration_ms=250,
        )
        assert log_id
        detail = log_store.get_by_id(log_id)
        assert detail is not None
        assert detail["model"] == "claude-4"
        assert detail["input_tokens"] == 100
        assert detail["output_tokens"] == 50
        assert detail["total_tokens"] == 150
        assert detail["cost"] == 0.02
        assert detail["status_code"] == 200
        assert detail["stream"] is False
        assert detail["duration_ms"] == 250

    def test_record_returns_empty_when_disabled(self, tmp_path):
        store = LogStore(str(tmp_path / "disabled.db"), enabled=False)
        log_id = store.record(
            timestamp=time.time(),
            session_id="s1",
            model="m1",
            provider="p1",
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            cost=0.0,
            status_code=200,
            request_body=None,
            response_body=None,
            request_path="/",
            stream=False,
            duration_ms=1,
        )
        assert log_id == ""

    def test_get_by_id_returns_none_for_missing(self, log_store):
        assert log_store.get_by_id("nonexistent") is None

    def test_get_by_id_parses_json_bodies(self, log_store):
        log_id = log_store.record(
            timestamp=time.time(),
            session_id="s1",
            model="m1",
            provider="p1",
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            cost=0.0,
            status_code=200,
            request_body='{"key": "value"}',
            response_body='{"id": "resp_123"}',
            request_path="/v1/responses",
            stream=False,
            duration_ms=10,
        )
        detail = log_store.get_by_id(log_id)
        assert detail["request_body"] == {"key": "value"}
        assert detail["response_body"] == {"id": "resp_123"}

    def test_get_by_id_handles_non_json_bodies(self, log_store):
        log_id = log_store.record(
            timestamp=time.time(),
            session_id="s1",
            model="m1",
            provider="p1",
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            cost=0.0,
            status_code=200,
            request_body="not json",
            response_body="also not json",
            request_path="/",
            stream=False,
            duration_ms=5,
        )
        detail = log_store.get_by_id(log_id)
        assert detail["request_body"] == "not json"
        assert detail["response_body"] == "also not json"


class TestQuery:
    def test_query_returns_rows_newest_first(self, log_store):
        for i in range(5):
            log_store.record(
                timestamp=time.time() - (5 - i),
                session_id="s1",
                model="m1",
                provider="p1",
                input_tokens=i,
                output_tokens=i,
                total_tokens=i * 2,
                cost=0.001 * i,
                status_code=200,
                request_body=None,
                response_body=None,
                request_path="/v1/responses",
                stream=False,
                duration_ms=i * 10,
            )
        rows, has_more = log_store.query(range_seconds=None, limit=10)
        assert len(rows) == 5
        assert has_more is False
        assert rows[0]["input_tokens"] >= rows[1]["input_tokens"]

    def test_query_pagination_cursor(self, log_store):
        for i in range(10):
            log_store.record(
                timestamp=time.time() - i,
                session_id="s1",
                model="m1",
                provider="p1",
                input_tokens=i,
                output_tokens=0,
                total_tokens=i,
                cost=0.0,
                status_code=200,
                request_body=None,
                response_body=None,
                request_path="/",
                stream=False,
                duration_ms=0,
            )
        page1, has_more1 = log_store.query(range_seconds=None, limit=3)
        assert len(page1) == 3
        assert has_more1 is True

        page2, has_more2 = log_store.query(
            range_seconds=None,
            before=page1[-1]["timestamp"],
            limit=3,
        )
        assert len(page2) == 3
        assert has_more2 is True
        assert page2[0]["timestamp"] < page1[-1]["timestamp"]

    def test_query_time_range_filter(self, log_store):
        now = time.time()
        log_store.record(
            timestamp=now - 100,
            session_id="s1",
            model="m1",
            provider="p1",
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            cost=0.0,
            status_code=200,
            request_body=None,
            response_body=None,
            request_path="/",
            stream=False,
            duration_ms=0,
        )
        log_store.record(
            timestamp=now - 100000,
            session_id="s1",
            model="m1",
            provider="p1",
            input_tokens=2,
            output_tokens=2,
            total_tokens=4,
            cost=0.0,
            status_code=200,
            request_body=None,
            response_body=None,
            request_path="/",
            stream=False,
            duration_ms=0,
        )
        rows, _ = log_store.query(range_seconds=3600, limit=10)
        assert len(rows) == 1
        assert rows[0]["input_tokens"] == 1


class TestRecordFromResponse:
    def test_record_from_response_extracts_usage(self, tmp_path):
        from mcptap.log_store import LogStore, record_from_response

        db_path = str(tmp_path / "helper.db")
        store = LogStore(db_path)
        store.migrate()
        store.connect()

        body_json = {
            "id": "resp_1",
            "model": "claude-4",
            "usage": {
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "cost": 0.015,
            },
        }
        import json as json_mod

        response_raw = json_mod.dumps(body_json).encode("utf-8")

        record_from_response(
            store,
            request_body={"model": "claude-4", "input": []},
            response_raw=response_raw,
            response_body_json=body_json,
            session_id="sess1",
            model="claude-4",
            provider="anthropic",
            status_code=200,
            request_path="/v1/responses",
            stream=False,
            start_time=time.time(),
        )

        rows, _ = store.query(range_seconds=3600, limit=10)
        assert len(rows) == 1
        assert rows[0]["model"] == "claude-4"
        assert rows[0]["input_tokens"] == 120
        assert rows[0]["output_tokens"] == 30
        assert rows[0]["total_tokens"] == 150
        assert rows[0]["cost"] == 0.015
        store.close()

    def test_record_from_response_handles_none_store(self):
        record_from_response(
            None,
            request_body={},
            response_raw=b"",
            response_body_json=None,
            session_id="s1",
            model="m1",
            provider="p1",
            status_code=200,
            request_path="/",
            stream=False,
            start_time=time.time(),
        )

    def test_record_from_response_handles_disabled_store(self, tmp_path):
        store = LogStore(str(tmp_path / "disabled.db"), enabled=False)
        record_from_response(
            store,
            request_body={},
            response_raw=b"",
            response_body_json=None,
            session_id="s1",
            model="m1",
            provider="p1",
            status_code=200,
            request_path="/",
            stream=False,
            start_time=time.time(),
        )


class TestTimeRanges:
    def test_time_range_map_has_all_presets(self):
        for val in ("15m", "30m", "1h", "3h", "24h", "48h", "1w"):
            assert val in TIME_RANGE_MAP
            assert TIME_RANGE_MAP[val] > 0
