"""Background task that periodically purges old log entries from SQLite.

Runs as an asyncio task inside the aiohttp event loop.  Every
``_PURGE_INTERVAL`` seconds it calls ``LogStore.purge_old()`` to delete
records older than ``settings.log_retention_days``.

Lifecycle mirrors ``ConfigReloader``: ``start()`` launches the task,
``stop()`` cancels it.
"""

import asyncio
from typing import Optional

from mcptap.log_store import LogStore
from mcptap.settings import LOGGER, settings

_PURGE_INTERVAL = 3600  # 1 hour


class LogRetentionTask:
    """Periodically purges old log entries from the SQLite store."""

    def __init__(self, log_store: LogStore) -> None:
        self._log_store = log_store
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.ensure_future(self._loop())
        LOGGER.info(
            "LogRetentionTask started (interval=%.0fs retention_days=%d)",
            _PURGE_INTERVAL,
            settings.log_retention_days,
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
        LOGGER.info("LogRetentionTask stopped")

    async def _loop(self) -> None:
        while True:
            try:
                self._purge_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.error("LogRetentionTask: purge error: %s", exc)
            await asyncio.sleep(_PURGE_INTERVAL)

    def _purge_once(self) -> None:
        if not self._log_store.enabled:
            return
        deleted = self._log_store.purge_old(settings.log_retention_days)
        if deleted:
            LOGGER.info("LogRetentionTask: purged %d old log entries", deleted)
