"""Tests for the periodic Pareto data download task."""

import asyncio
import json
from pathlib import Path

import pytest  # type: ignore

from mcptap.pareto_data import ParetoDataTask


@pytest.mark.asyncio
async def test_fetch_and_store_once_writes_valid_json_atomically(tmp_path: Path):
    target = tmp_path / "data" / "pareto.json"
    payload = {"models": [{"model": "test-model", "score": 1}]}
    task = ParetoDataTask(target_path=target)

    async def fetch_remote():
        return json.dumps(payload).encode("utf-8")

    task._fetch_remote = fetch_remote

    await task._fetch_and_store_once()

    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert sorted(target.parent.iterdir()) == [target]


@pytest.mark.asyncio
async def test_fetch_and_store_once_rejects_invalid_json_without_overwriting(tmp_path: Path):
    target = tmp_path / "pareto.json"
    target.write_text('{"previous": true}', encoding="utf-8")
    task = ParetoDataTask(target_path=target)

    async def fetch_remote():
        return b"not json"

    task._fetch_remote = fetch_remote

    with pytest.raises(ValueError, match="valid JSON object"):
        await task._fetch_and_store_once()

    assert json.loads(target.read_text(encoding="utf-8")) == {"previous": True}


@pytest.mark.asyncio
async def test_loop_continues_after_download_error(tmp_path: Path, monkeypatch):
    task = ParetoDataTask(target_path=tmp_path / "pareto.json")
    calls = 0

    async def fetch_and_store():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")

    monkeypatch.setattr(task, "_fetch_and_store_once", fetch_and_store)
    monkeypatch.setattr("mcptap.pareto_data._PARETO_INTERVAL", 0.01)

    task.start()
    await asyncio.sleep(0.05)
    await task.stop()

    assert calls >= 2


@pytest.mark.asyncio
async def test_start_stop_lifecycle(tmp_path: Path):
    task = ParetoDataTask(target_path=tmp_path / "pareto.json")

    task.start()
    assert task._task is not None
    await task.stop()
    assert task._task is None
