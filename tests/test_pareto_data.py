"""Tests for the periodic Pareto data download task."""

import asyncio
import json
from pathlib import Path

import pytest  # type: ignore

from mcptap.pareto_data import ParetoDataTask, _github_headers


@pytest.mark.asyncio
async def test_fetch_remote_sends_github_token(monkeypatch):
    captured = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def read(self):
            return b"{}"

    class FakeSession:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        def get(self, url, *, headers):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setenv("MCPTAP_GITHUB_TOKEN", "test-token")
    monkeypatch.setattr("mcptap.pareto_data.aiohttp.ClientSession", FakeSession)

    result = await ParetoDataTask._fetch_remote(
        "https://raw.githubusercontent.com/PCODE-pl/MCPTap-Pareto/dev/pareto.json"
    )

    assert result == b"{}"
    assert captured["headers"] == {
        "Accept": "application/vnd.github.raw+json",
        "Authorization": "Bearer test-token",
    }


def test_github_headers_require_token(monkeypatch):
    monkeypatch.delenv("MCPTAP_GITHUB_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="MCPTAP_GITHUB_TOKEN"):
        _github_headers("https://raw.githubusercontent.com/PCODE-pl/MCPTap-Pareto/dev/pareto.json")


def test_github_headers_are_not_added_to_other_hosts(monkeypatch):
    monkeypatch.setenv("MCPTAP_GITHUB_TOKEN", "test-token")

    assert _github_headers("https://example.com/pareto.json") == {}


@pytest.mark.asyncio
async def test_fetch_and_store_once_writes_valid_json_atomically(tmp_path: Path):
    target = tmp_path / "data" / "pareto.json"
    payload = {"models": [{"model": "test-model", "score": 1}]}
    task = ParetoDataTask(target_path=target)

    async def fetch_remote(url=None):
        return json.dumps(payload).encode("utf-8")

    task._fetch_remote = fetch_remote  # type: ignore[method-assign]

    await task._fetch_and_store_once()

    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert sorted(target.parent.iterdir()) == [target]


@pytest.mark.asyncio
async def test_fetch_and_store_once_rejects_invalid_json_without_overwriting(tmp_path: Path):
    target = tmp_path / "pareto.json"
    target.write_text('{"previous": true}', encoding="utf-8")
    task = ParetoDataTask(target_path=target)

    async def fetch_remote(url=None):
        return b"not json"

    task._fetch_remote = fetch_remote  # type: ignore[method-assign]

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


@pytest.mark.asyncio
async def test_tested_models_task_fetches_tested_models_url(tmp_path: Path):
    target = tmp_path / "tested_models.json"
    payload = {"providers": ["openrouter"], "free": {}, "paid": {}}
    task = ParetoDataTask(target_path=target, source_url=ParetoDataTask.TESTED_MODELS_URL)

    async def fetch_remote(url=None):
        return json.dumps(payload).encode("utf-8")

    task._fetch_remote = fetch_remote  # type: ignore[method-assign]

    await task._fetch_and_store_once()

    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert ParetoDataTask.TESTED_MODELS_URL.endswith("/tested_models.json")
    assert "MCPTap-Pareto" in ParetoDataTask.TESTED_MODELS_URL


@pytest.mark.asyncio
async def test_tested_models_task_rejects_non_object_payload(tmp_path: Path):
    target = tmp_path / "tested_models.json"
    task = ParetoDataTask(target_path=target, source_url=ParetoDataTask.TESTED_MODELS_URL)

    async def fetch_remote(url=None):
        return b"[1, 2, 3]"

    task._fetch_remote = fetch_remote  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="valid JSON object"):
        await task._fetch_and_store_once()

    assert not target.exists()
