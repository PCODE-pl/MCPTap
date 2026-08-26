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
        assert "zoomOnMouseWheel" not in body
        assert "getZr()" in body
        assert "convertFromPixel" in body
        assert "wheelDelta" in body
        assert "const nextYMin" in body
        assert "const nextYMax" in body
        assert "Math.max(baseRange.yMin, nextYMin)" in body
        assert "Math.min(baseRange.yMax, nextYMax)" in body
        assert "notMerge: true" in body
        assert 'data-testid="pareto-page"' in body
        assert 'data-testid="pareto-content-layout"' in body
        assert 'data-testid="quality-controls"' in body
        assert ':data-testid="`quality-slider-${control.key}`"' in body
        assert "const QUALITY_SLIDER_DEFINITIONS = [" in body
        assert "{ key: 'accuracy', label: 'Accuracy' }" in body
        assert "{ key: 'uptime-short', label: 'Uptime short' }" in body
        assert "{ key: 'uptime-long', label: 'Uptime long' }" in body
        assert "{ key: 'latency-short', label: 'Latency short' }" in body
        assert "{ key: 'latency-long', label: 'Latency long' }" in body
        assert "{ key: 'throughput-short', label: 'Throughput short' }" in body
        assert "{ key: 'throughput-long', label: 'Throughput long' }" in body
        assert "const QUALITY_SLIDER_MAX = 3;" in body
        assert "function interpolateWeights(start, end, count)" in body
        assert "qualityWeights.accuracy = qualityControls.accuracy" in body
        assert "qualityWeights[key] = values[index]" in body
        assert "qualityWeights.uptime" not in body
        assert "const scheduleRenderChart = debounce(() => renderChart(), 100);" in body
        assert "<h1" not in body
        assert 'data-testid="pareto-chart"' in body
        assert 'data-testid="pareto-zoom-area"' not in body
        assert "const areaZoomActive = ref(true);" in body
        assert "areaZoomActive.value = false" not in body
        assert 'data-testid="pareto-zoom-reset"' in body
        assert 'data-testid="pareto-save-image"' not in body
        assert "saveChartImage" not in body
        assert 'role="toolbar"' in body
        assert 'aria-label="Pareto scatter chart showing weighted cost and quality"' in body
        assert "new URLSearchParams(window.location.search).get('e2llm') === '2'" in body
        assert "const hasProviderFilter = selectedProviders.value.length > 0;" in body
        assert "if (hasProviderFilter && !selectedProviders.value.includes(provider)) continue;" in body
        assert "data-chart-state" in body
        assert "data-renderer" in body
        assert "data-canvas-count" in body
        assert "data-series-count" in body
        assert "data-x-min" in body
        assert "data-x-max" in body
        assert "data-zoom-active" in body
        assert 'data-testid="pareto-chart-diagnostics"' in body
        assert 'v-if="diagnosticsVisible"' in body
        assert "lazyUpdate: false" in body
        assert "lazyUpdate: true" not in body
