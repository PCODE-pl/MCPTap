"""Tests for the LogRetentionTask background purge task."""

import asyncio
import os
import sys
import time
from unittest.mock import patch

import pytest  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcptap.log_retention import LogRetentionTask
from mcptap.log_store import LogStore


@pytest.fixture
def log_store(tmp_path):
    db_path = str(tmp_path / "retention_test.db")
    store = LogStore(db_path)
    store.migrate()
    store.connect()
    yield store
    store.close()


def _insert_entry(store: LogStore, age_seconds: float, session_id: str = "s") -> None:
    store.record(
        timestamp=time.time() - age_seconds,
        session_id=session_id,
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


class TestLogRetentionTask:
    def test_purge_once_deletes_old_entries(self, log_store):
        _insert_entry(log_store, age_seconds=40 * 86400, session_id="old")
        _insert_entry(log_store, age_seconds=100, session_id="recent")

        task = LogRetentionTask(log_store)
        task._purge_once()

        rows, _ = log_store.query(range_seconds=None, limit=10)
        assert len(rows) == 1
        assert rows[0]["session_id"] == "recent"

    def test_purge_once_skips_disabled_store(self, tmp_path):
        store = LogStore(str(tmp_path / "disabled.db"), enabled=False)
        task = LogRetentionTask(store)
        # Should not raise
        task._purge_once()

    def test_start_stop_lifecycle(self, log_store):
        async def run():
            task = LogRetentionTask(log_store)
            task.start()
            assert task._task is not None
            await task.stop()
            assert task._task is None

        asyncio.run(run())

    def test_loop_purges_on_each_iteration(self, log_store):
        """Verify the loop calls _purge_once and sleeps between iterations."""
        _insert_entry(log_store, age_seconds=40 * 86400, session_id="old")

        call_count = 0
        original_purge = log_store.purge_old

        def counting_purge(days):
            nonlocal call_count
            call_count += 1
            return original_purge(days)

        task = LogRetentionTask(log_store)

        async def run_briefly():
            task.start()
            # Let the first iteration execute
            await asyncio.sleep(0.05)
            await task.stop()

        with patch.object(log_store, "purge_old", side_effect=counting_purge):
            with patch("mcptap.log_retention._PURGE_INTERVAL", 0.01):
                asyncio.run(run_briefly())

        assert call_count >= 1
        rows, _ = log_store.query(range_seconds=None, limit=10)
        assert len(rows) == 0

    def test_loop_continues_after_error(self, log_store):
        """The loop should survive a transient error in purge_old."""
        _insert_entry(log_store, age_seconds=40 * 86400, session_id="old")

        call_count = 0
        original_purge = log_store.purge_old

        def flaky_purge(days):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient failure")
            return original_purge(days)

        with patch.object(log_store, "purge_old", side_effect=flaky_purge):
            task = LogRetentionTask(log_store)

            async def run_briefly():
                task.start()
                await asyncio.sleep(0.1)
                await task.stop()

            with patch("mcptap.log_retention._PURGE_INTERVAL", 0.01):
                asyncio.run(run_briefly())

        assert call_count >= 2
        rows, _ = log_store.query(range_seconds=None, limit=10)
        assert len(rows) == 0
