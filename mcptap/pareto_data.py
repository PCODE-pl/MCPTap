"""Periodically refresh Pareto model data from the upstream repository."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

import aiohttp  # type: ignore

from mcptap.settings import LOGGER

_PARETO_URL = "https://raw.githubusercontent.com/PCODE-pl/MCPTap-Pareto/dev/pareto.json"
_PARETO_INTERVAL = 3600
_FETCH_TIMEOUT = 30
_DEFAULT_TARGET_PATH = Path(__file__).resolve().parent.parent / "data" / "pareto.json"


class ParetoDataTask:
    """Download and atomically store Pareto data at a fixed interval."""

    def __init__(self, target_path: Path = _DEFAULT_TARGET_PATH) -> None:
        self._target_path = target_path
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.ensure_future(self._loop())
        LOGGER.info(
            "ParetoDataTask started (interval=%.0fs target=%s)",
            _PARETO_INTERVAL,
            self._target_path,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        LOGGER.info("ParetoDataTask stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self._fetch_and_store_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.error("ParetoDataTask: refresh error: %s", exc)
            await asyncio.sleep(_PARETO_INTERVAL)

    async def _fetch_and_store_once(self) -> None:
        raw_data = await self._fetch_remote()
        try:
            payload = json.loads(raw_data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Pareto data must be valid JSON object") from exc
        if not isinstance(payload, dict):
            raise ValueError("Pareto data must be valid JSON object")

        self._target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self._target_path.parent,
                prefix=f".{self._target_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(raw_data)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self._target_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        LOGGER.info("ParetoDataTask: refreshed %s", self._target_path)

    @staticmethod
    async def _fetch_remote() -> bytes:
        timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(_PARETO_URL) as response:
                if response.status != 200:
                    body = await response.text()
                    raise RuntimeError(f"HTTP {response.status} from {_PARETO_URL}: {body[:200]}")
                return await response.read()
