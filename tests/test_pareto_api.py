"""Tests for the Pareto data HTTP handlers."""

from pathlib import Path

import pytest  # type: ignore
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from mcptap.pareto_api import handle_pareto_data, serve_pareto_page


@pytest.mark.asyncio
async def test_handle_pareto_data_returns_json_from_configured_path(tmp_path: Path):
    target = tmp_path / "pareto.json"
    target.write_text('{"model": {"accuracy": 90}}', encoding="utf-8")
    app = web.Application()
    app["pareto_path"] = target
    app.router.add_get("/api/pareto", handle_pareto_data)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/pareto")
        assert response.status == 200
        assert await response.json() == {"model": {"accuracy": 90}}


@pytest.mark.asyncio
async def test_handle_pareto_data_returns_not_found_when_file_is_missing(tmp_path: Path):
    app = web.Application()
    app["pareto_path"] = tmp_path / "missing.json"
    app.router.add_get("/api/pareto", handle_pareto_data)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/pareto")
        assert response.status == 404
        assert await response.json() == {"error": "Pareto data not found"}


@pytest.mark.asyncio
async def test_serve_pareto_page_returns_html():
    app = web.Application()
    app.router.add_get("/ui/pareto", serve_pareto_page)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/ui/pareto")
        assert response.status == 200
        body = await response.text()
        assert "MCPTap Pareto" in body
        assert "weightedCost" in body
        assert "contentSize" in body
        assert "max-height: calc(100vh - 24px)" in body
        assert "getBoundingClientRect" in body
        assert "type: 'inside'" in body
        assert "xAxisIndex: 0" in body
        assert "yAxisIndex: 0" in body
        assert "zoomOnMouseWheel: true" in body
        assert "moveOnMouseMove: true" in body
        assert "itemGap: 10" in body
        assert "xAxisIndex: 'all'" not in body
        assert "yAxisIndex: 'all'" not in body
