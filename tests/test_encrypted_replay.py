"""Tests for model-bound encrypted Responses API replay handling."""

import json
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # type: ignore
from aiohttp import web  # type: ignore
from aiohttp.test_utils import TestClient, TestServer  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcptap.encrypted_replay import (  # noqa: E402
    ReplayItemRemoval,
    encrypted_replay_hashes_from_payload,
    filter_encrypted_replay_items,
    log_replay_sanitization,
)
from mcptap.session import SessionTracker  # noqa: E402
from mcptap.upstream import post_upstream_buffered_with_replay_retry  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::aiohttp.web_exceptions.NotAppKeyWarning")


def _encrypted_item(item_type: str, content: str) -> dict:
    return {
        "type": item_type,
        "encrypted_content": content,
    }


class TestEncryptedReplayFiltering:
    def test_filter_removes_only_unrecognized_encrypted_items(self):
        accepted_reasoning = _encrypted_item("reasoning", "accepted-reasoning")
        rejected_reasoning = _encrypted_item("reasoning", "rejected-reasoning")
        rejected_compaction = _encrypted_item("compaction", "rejected-compaction")
        payload = {
            "input": [
                {"role": "user", "content": "keep"},
                accepted_reasoning,
                rejected_reasoning,
                rejected_compaction,
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "keep"}]},
                {"type": "function_call", "call_id": "call_1", "name": "keep", "arguments": "{}"},
            ]
        }

        removal = filter_encrypted_replay_items(
            payload,
            encrypted_replay_hashes_from_payload({"input": [accepted_reasoning]}),
        )

        assert removal == ReplayItemRemoval(reasoning=1, compaction=1)
        assert payload["input"] == [
            {"role": "user", "content": "keep"},
            accepted_reasoning,
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "keep"}]},
            {"type": "function_call", "call_id": "call_1", "name": "keep", "arguments": "{}"},
        ]

    @pytest.mark.asyncio
    async def test_session_filters_replay_after_route_change(self):
        tracker = SessionTracker()
        terra_route = "terra-route"
        luna_route = "luna-route"
        terra_item = _encrypted_item("reasoning", "terra-encrypted")

        await tracker.record_encrypted_replay(
            "session-1",
            terra_route,
            {"input": [terra_item]},
            {"output": [terra_item]},
        )

        payload = {"input": [terra_item, {"role": "user", "content": "continue"}]}
        plan = await tracker.prepare_encrypted_replay("session-1", luna_route, payload)

        assert plan.retry_on_404 is False
        assert plan.previous_route_fingerprint == terra_route
        assert plan.removal == ReplayItemRemoval(reasoning=1)
        assert payload["input"] == [{"role": "user", "content": "continue"}]

    @pytest.mark.asyncio
    async def test_unknown_session_uses_single_404_retry_path(self):
        tracker = SessionTracker()
        payload = {"input": [_encrypted_item("reasoning", "unknown-encrypted")]}

        plan = await tracker.prepare_encrypted_replay("session-1", "luna-route", payload)

        assert plan.retry_on_404 is True
        assert plan.removal == ReplayItemRemoval()
        assert len(payload["input"]) == 1


class TestEncryptedReplayRetry:
    @pytest.mark.asyncio
    async def test_retry_removes_encrypted_items_after_matching_404(self):
        payload = {
            "model": "luna",
            "input": [
                _encrypted_item("reasoning", "reasoning-to-remove"),
                _encrypted_item("compaction", "compaction-to-remove"),
                {"role": "user", "content": "continue"},
            ],
        }
        rejection = {
            "error": {
                "message": (
                    "Your request contains encrypted reasoning or compaction content that was produced "
                    "under a different model. Encrypted payloads can only be replayed to the endpoint "
                    "that created them."
                )
            }
        }
        success = {"id": "resp_1", "output": []}
        mock_post = AsyncMock(
            side_effect=[
                (404, {"Content-Type": "application/json"}, json.dumps(rejection).encode("utf-8"), rejection),
                (200, {"Content-Type": "application/json"}, json.dumps(success).encode("utf-8"), success),
            ]
        )

        with patch("mcptap.upstream.post_upstream_buffered", mock_post):
            status, _headers, _raw, body_json, removal = await post_upstream_buffered_with_replay_retry(
                session=AsyncMock(),
                path="/v1/responses",
                headers={"Authorization": "Bearer test"},
                body=payload,
                stream=False,
            )

        assert status == 200
        assert body_json == success
        assert removal == ReplayItemRemoval(reasoning=1, compaction=1)
        assert payload["input"] == [{"role": "user", "content": "continue"}]
        assert mock_post.await_count == 2

    @pytest.mark.asyncio
    async def test_retry_does_not_run_for_another_404(self):
        payload = {"model": "luna", "input": [_encrypted_item("reasoning", "keep")]}
        rejection = {"error": {"message": "Unknown response ID"}}
        mock_post = AsyncMock(
            return_value=(404, {"Content-Type": "application/json"}, json.dumps(rejection).encode("utf-8"), rejection)
        )

        with patch("mcptap.upstream.post_upstream_buffered", mock_post):
            status, _headers, _raw, body_json, removal = await post_upstream_buffered_with_replay_retry(
                session=AsyncMock(),
                path="/v1/responses",
                headers={"Authorization": "Bearer test"},
                body=payload,
                stream=False,
            )

        assert status == 404
        assert body_json == rejection
        assert removal == ReplayItemRemoval()
        assert len(payload["input"]) == 1
        assert mock_post.await_count == 1


class TestProxyEncryptedReplay:
    @pytest.mark.asyncio
    async def test_proxy_filters_previous_route_replay_before_forwarding(self):
        from mcptap.app import proxy

        tracker = SessionTracker()
        stale_item = _encrypted_item("reasoning", "terra-encrypted")
        await tracker.record_encrypted_replay(
            "session-1",
            "terra-route",
            {"input": [stale_item]},
            {"output": [stale_item]},
        )
        seen_payloads = []

        def rewrite_payload(_request, payload, _intercept, _per_model_config):
            payload["model"] = "luna"
            return "client-model", "luna", None

        async def forward_payload(_request, _session, _target_url, _headers, payload):
            seen_payloads.append(payload.copy())
            return web.Response(status=200), b'{"output":[]}'

        app = web.Application()
        app["client_session"] = MagicMock()
        app["mcp_intercept"] = MagicMock(enabled=False)
        app["per_model_config"] = {}
        app["hook_gateway"] = MagicMock(enabled=False)
        app["session_tracker"] = tracker
        app.router.add_post("/v1/responses", proxy)

        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            with patch("mcptap.app.rewrite_json_payload", side_effect=rewrite_payload):
                with patch("mcptap.app.forward_rewritten", side_effect=forward_payload):
                    response = await client.post(
                        "/v1/responses",
                        headers={"session-id": "session-1"},
                        json={"model": "client-model", "input": [stale_item, {"role": "user", "content": "continue"}]},
                    )
                    assert response.status == 200
        finally:
            await client.close()

        assert seen_payloads[0]["input"] == [{"role": "user", "content": "continue"}]


class TestEncryptedReplayLogging:
    def test_debug_log_contains_hashes_and_counts_without_encrypted_content(self, caplog):
        logger = logging.getLogger("mcptap")
        caplog.set_level(logging.DEBUG, logger="mcptap")

        log_replay_sanitization(
            logger,
            session_id="session-1",
            removal=ReplayItemRemoval(reasoning=2, compaction=1),
            previous_route_fingerprint="old-route-hash",
            route_fingerprint="new-route-hash",
            reason="model_route_changed",
        )

        assert "removed_reasoning=2" in caplog.text
        assert "removed_compaction=1" in caplog.text
        assert "old_route_hash=old-route-hash" in caplog.text
        assert "new_route_hash=new-route-hash" in caplog.text
        assert "reason=model_route_changed" in caplog.text
        assert "encrypted_content" not in caplog.text
