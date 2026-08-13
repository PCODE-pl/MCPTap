"""Regression tests for provider credit cross-validation."""

import os
import sys
import time

import pytest  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcptap.credits_checker import CreditsCheckerTask
from mcptap.log_store import LogStore


@pytest.fixture
def log_store(tmp_path):
    store = LogStore(str(tmp_path / "credits.db"))
    store.migrate()
    store.connect()
    yield store
    store.close()


@pytest.mark.asyncio
async def test_api_failure_does_not_reset_credit_baseline(log_store, monkeypatch):
    """A transient API failure must not create a false cumulative mismatch."""
    now = time.time()
    log_store.insert_credit_snapshot(
        provider="openrouter",
        credits_url="https://credits.test",
        fetched_at=now - 20,
        total_credits=900.0,
        total_usage=100.0,
        local_cost_sum=0.0,
        request_count=0,
        discrepancy=0.0,
        status="ok",
    )
    log_store.record(
        timestamp=now - 10,
        session_id="session-1",
        model="test-model",
        provider="openrouter",
        input_tokens=10,
        output_tokens=10,
        total_tokens=20,
        cost=1.0,
        status_code=200,
        request_body=None,
        response_body=None,
        request_path="/v1/responses",
        stream=False,
        duration_ms=10,
    )

    task = CreditsCheckerTask(log_store)
    task._active_provider = "openrouter"
    task._active_credits_url = "https://credits.test"
    task._active_credits_api_key = "test-key"

    responses = iter(
        [
            RuntimeError("temporary network failure"),
            {"total_credits": 900.0, "total_usage": 101.0},
        ]
    )

    async def fetch_credits(_url, _api_key):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(task, "_fetch_credits", fetch_credits)

    await task._check_once()
    await task._check_once()

    snapshot = log_store.get_last_credit_snapshot("openrouter")
    assert snapshot is not None
    assert snapshot["status"] == "ok"
    assert snapshot["total_usage"] == pytest.approx(101.0)
    assert snapshot["discrepancy"] == pytest.approx(0.0)
