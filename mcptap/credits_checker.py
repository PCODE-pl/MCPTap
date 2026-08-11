"""Periodic credits cross-validation for upstream providers.

Fetches the provider's credits API, computes local cost from the SQLite
request log since the last snapshot, and stores a comparison row in
``credit_snapshots``.  Discrepancies above the configured threshold are
logged as warnings so the operator can investigate upstream billing drift.

Runs as an asyncio task inside the aiohttp event loop.  Lifecycle mirrors
``LogRetentionTask``: ``start()`` launches the task, ``stop()`` cancels it.
"""

import asyncio
import time
from typing import Any, Dict, Optional

import aiohttp  # type: ignore

from mcptap.log_store import LogStore
from mcptap.settings import LOGGER, settings


class CreditsCheckerTask:
    """Periodically checks provider credits API vs local cost totals."""

    def __init__(self, log_store: LogStore) -> None:
        self._log_store = log_store
        self._task: Optional[asyncio.Task[None]] = None
        self._active_provider: str = settings.upstream_provider
        self._active_credits_url: str = settings.credits_url
        self._active_credits_api_key: str = settings.credits_api_key or settings.api_key

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the background polling task."""
        if self._task is not None:
            return
        if not self._active_credits_url:
            LOGGER.info(
                "CreditsCheckerTask skipped (MCP_TAP_CREDITS_URL is empty for %s)",
                self._active_provider,
            )
            return
        self._task = asyncio.ensure_future(self._loop())
        LOGGER.info(
            "CreditsCheckerTask started (provider=%s interval=%ds threshold=$%.4f)",
            self._active_provider,
            settings.credits_check_interval,
            settings.credits_discrepancy_threshold,
        )

    async def stop(self) -> None:
        """Cancel the polling task and wait for it to finish."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        LOGGER.info("CreditsCheckerTask stopped")

    # ------------------------------------------------------------------
    # Provider switch handling (called from config_reloader)
    # ------------------------------------------------------------------

    def on_provider_changed(self) -> None:
        """Re-read active provider from settings and restart if needed.

        Called by ``reload_env_and_propagate`` after ``reload_settings()``.
        If the provider or credits URL changed, the task is restarted.
        """
        old_provider = self._active_provider
        new_provider = settings.upstream_provider
        new_url = settings.credits_url

        if old_provider == new_provider and self._active_credits_url == new_url:
            return

        LOGGER.info(
            "CreditsCheckerTask: provider changed %s -> %s (url=%s)",
            old_provider,
            new_provider,
            new_url or "(empty)",
        )
        self._active_provider = new_provider
        self._active_credits_url = new_url
        self._active_credits_api_key = settings.credits_api_key or settings.api_key

        # Restart the task with the new provider context
        if self._task is not None:
            # Snapshot the old provider before restarting
            self._snapshot_now(self._log_store, old_provider)
            self._task.cancel()
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're inside the event loop; schedule cancellation
                    pass
            except RuntimeError:
                pass
            self._task = None

        if self._active_credits_url:
            self._task = asyncio.ensure_future(self._loop())
            LOGGER.info(
                "CreditsCheckerTask restarted (provider=%s interval=%ds)",
                self._active_provider,
                settings.credits_check_interval,
            )

    def snapshot_if_active(self) -> None:
        """Take a snapshot for the current provider if checker is running.

        Called before env reload so the old provider's data is captured
        before the credits URL / API key become stale.
        """
        if self._active_credits_url:
            self._snapshot_now(self._log_store, self._active_provider)

    # ------------------------------------------------------------------
    # Internal: polling loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Main polling loop — runs until cancelled."""
        while True:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.error("CreditsCheckerTask: check error: %s", exc)
            await asyncio.sleep(settings.credits_check_interval)

    # ------------------------------------------------------------------
    # Internal: single check cycle
    # ------------------------------------------------------------------

    async def _check_once(self) -> None:
        """Fetch remote credits, compare with local cost, store snapshot."""
        provider = self._active_provider
        credits_url = self._active_credits_url
        api_key = self._active_credits_api_key

        if not credits_url:
            return

        # 1. Fetch remote credits
        try:
            remote = await self._fetch_credits(credits_url, api_key)
        except Exception as exc:
            LOGGER.warning(
                "CreditsCheckerTask: fetch failed for %s: %s",
                provider,
                exc,
            )
            # Store error snapshot with zeroed remote values
            self._store_snapshot(
                provider=provider,
                credits_url=credits_url,
                total_credits=0.0,
                total_usage=0.0,
                status="error",
            )
            await self._send_telegram_alert(
                provider=provider,
                status="error",
                remote_usage=0.0,
                expected_usage=0.0,
                local_sum=0.0,
                request_count=0,
                discrepancy=0.0,
                threshold=0.0,
            )
            return

        # 2. Compute local cost since last snapshot
        last = self._log_store.get_last_credit_snapshot(provider)
        since = last["fetched_at"] if last else 0.0
        local_sum, request_count = self._log_store.sum_cost_since(provider, since)

        # 3. Compute discrepancy
        #    remote total_usage vs. previous remote total_usage + local cost
        prev_usage = last["total_usage"] if last else 0.0
        expected_usage = prev_usage + local_sum
        discrepancy = remote["total_usage"] - expected_usage

        # 4. Determine status
        threshold = settings.credits_discrepancy_threshold
        status = "ok" if abs(discrepancy) <= threshold else "mismatch"

        if status == "mismatch":
            LOGGER.warning(
                "CreditsCheckerTask: CREDIT MISMATCH provider=%s "
                "remote_usage=$%.4f expected=$%.4f local_sum=$%.4f "
                "requests=%d discrepancy=$%.4f (threshold=$%.4f)",
                provider,
                remote["total_usage"],
                expected_usage,
                local_sum,
                request_count,
                discrepancy,
                threshold,
            )
            await self._send_telegram_alert(
                provider=provider,
                status="mismatch",
                remote_usage=remote["total_usage"],
                expected_usage=expected_usage,
                local_sum=local_sum,
                request_count=request_count,
                discrepancy=discrepancy,
                threshold=threshold,
            )
        elif request_count > 0:
            LOGGER.info(
                "CreditsCheckerTask: ok provider=%s remote_usage=$%.4f local_sum=$%.4f requests=%d",
                provider,
                remote["total_usage"],
                local_sum,
                request_count,
            )

        # 5. Store snapshot
        self._store_snapshot(
            provider=provider,
            credits_url=credits_url,
            total_credits=remote["total_credits"],
            total_usage=remote["total_usage"],
            local_cost_sum=local_sum,
            request_count=request_count,
            discrepancy=discrepancy,
            status=status,
        )

    # ------------------------------------------------------------------
    # Internal: snapshot for provider switch (synchronous path)
    # ------------------------------------------------------------------

    def _snapshot_now(self, log_store: LogStore, provider: str) -> None:
        """Take a best-effort snapshot for *provider* (sync, fire-and-forget).

        Used during provider switches where we can't await an async fetch.
        Stores only local cost data; remote values are zeroed.
        """
        if not self._active_credits_url or not provider:
            return
        try:
            last = log_store.get_last_credit_snapshot(provider)
            since = last["fetched_at"] if last else 0.0
            local_sum, request_count = log_store.sum_cost_since(provider, since)
            if request_count == 0:
                return
            log_store.insert_credit_snapshot(
                provider=provider,
                credits_url=self._active_credits_url,
                fetched_at=time.time(),
                total_credits=0.0,
                total_usage=0.0,
                local_cost_sum=local_sum,
                request_count=request_count,
                discrepancy=0.0,
                status="switch_snapshot",
            )
            LOGGER.info(
                "CreditsCheckerTask: switch snapshot provider=%s local_sum=$%.4f requests=%d",
                provider,
                local_sum,
                request_count,
            )
        except Exception as exc:
            LOGGER.debug(
                "CreditsCheckerTask: switch snapshot failed for %s: %s",
                provider,
                exc,
            )

    # ------------------------------------------------------------------
    # Internal: store snapshot
    # ------------------------------------------------------------------

    def _store_snapshot(
        self,
        *,
        provider: str,
        credits_url: str,
        total_credits: float,
        total_usage: float,
        local_cost_sum: float = 0.0,
        request_count: int = 0,
        discrepancy: float = 0.0,
        status: str = "ok",
    ) -> None:
        """Insert a credit snapshot into the database."""
        try:
            self._log_store.insert_credit_snapshot(
                provider=provider,
                credits_url=credits_url,
                fetched_at=time.time(),
                total_credits=total_credits,
                total_usage=total_usage,
                local_cost_sum=local_cost_sum,
                request_count=request_count,
                discrepancy=discrepancy,
                status=status,
            )
        except Exception as exc:
            LOGGER.error(
                "CreditsCheckerTask: failed to store snapshot for %s: %s",
                provider,
                exc,
            )

    # ------------------------------------------------------------------
    # Internal: HTTP fetch
    # ------------------------------------------------------------------

    @staticmethod
    async def _fetch_credits(
        url: str,
        api_key: str,
    ) -> Dict[str, Any]:
        """Fetch credits from the provider API.

        Returns a dict with at least ``total_credits`` and ``total_usage``.
        Raises on HTTP errors or network failures.
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
        ) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status} from {url}: {body[:200]}")
                data = await resp.json()
        # OpenRouter wraps in {"data": {...}}
        payload = data.get("data", data) if isinstance(data, dict) else data
        return {
            "total_credits": float(payload.get("total_credits", 0) or 0),
            "total_usage": float(payload.get("total_usage", 0) or 0),
        }

    # ------------------------------------------------------------------
    # Internal: Telegram alert
    # ------------------------------------------------------------------

    @staticmethod
    def _should_alert(status: str) -> bool:
        """Check whether the current status warrants a Telegram alert."""
        level = settings.telegram_alert_level
        if not level or not settings.telegram_bot_token or not settings.telegram_chat_id:
            return False
        if level == "all":
            return True
        if level == "error" and status in ("mismatch", "error"):
            return True
        if level == "mismatch" and status == "mismatch":
            return True
        return False

    @staticmethod
    async def _send_telegram_alert(
        *,
        provider: str,
        status: str,
        remote_usage: float,
        expected_usage: float,
        local_sum: float,
        request_count: int,
        discrepancy: float,
        threshold: float,
    ) -> None:
        """Send a Telegram message about a credit discrepancy or error."""
        if not CreditsCheckerTask._should_alert(status):
            return

        if status == "mismatch":
            emoji = "\u26a0\ufe0f"
            title = "Credit Mismatch"
            body = (
                f"<b>{title}</b>\n\n"
                f"<b>Provider:</b> {provider}\n"
                f"<b>Remote usage:</b> ${remote_usage:.4f}\n"
                f"<b>Expected:</b> ${expected_usage:.4f}\n"
                f"<b>Local cost sum:</b> ${local_sum:.4f}\n"
                f"<b>Requests:</b> {request_count}\n"
                f"<b>Discrepancy:</b> ${discrepancy:.4f}\n"
                f"<b>Threshold:</b> ${threshold:.4f}"
            )
        else:
            emoji = "\u274c"
            title = "Credit Check Error"
            body = (
                f"<b>{title}</b>\n\n"
                f"<b>Provider:</b> {provider}\n"
                f"<b>Status:</b> {status}\n"
                f"<b>Note:</b> Could not fetch credits from API"
            )

        text = f"{emoji} {body}"
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            ) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        body_text = await resp.text()
                        LOGGER.warning(
                            "CreditsCheckerTask: Telegram alert failed (HTTP %d): %s",
                            resp.status,
                            body_text[:200],
                        )
                    else:
                        LOGGER.info("CreditsCheckerTask: Telegram alert sent for %s", provider)
        except Exception as exc:
            LOGGER.warning("CreditsCheckerTask: Telegram alert failed: %s", exc)
