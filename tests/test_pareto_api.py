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
        assert "const nextXMin = anchor[0] - (anchor[0] - viewRange.value.xMin) * factor;" in body
        assert "const nextXMax = anchor[0] + (viewRange.value.xMax - anchor[0]) * factor;" in body
        assert "xMin: Math.max(baseRange.xMin, anchoredXRange.min)" in body
        assert "xMax: Math.min(baseRange.xMax, anchoredXRange.max)" in body
        assert "let zeroPriceXMin = null;" in body
        assert "const nextXMinWithZeroPrice = zeroPriceXMin === null" in body
        assert "zeroPriceXMin = -Math.max" in body
        assert "function preserveZoomAnchor(anchor, currentRange, nextMin, nextMax)" in body
        assert "const anchorRatio = (anchor - currentRange.min) / (currentRange.max - currentRange.min);" in body
        assert "nextMax = nextMin + (anchor - nextMin) * (1 - anchorRatio) / anchorRatio;" in body
        assert (
            "const anchoredXRange = preserveZoomAnchor(anchor[0], { min: viewRange.value.xMin, max: viewRange.value.xMax }, nextXMinWithZeroPrice, nextXMax);"
            in body
        )
        assert "xMin: Math.max(baseRange.xMin, anchoredXRange.min)" in body
        assert "xMax: Math.min(baseRange.xMax, anchoredXRange.max)" in body
        assert "notMerge: true" in body
        assert 'data-testid="pareto-page"' in body
        assert 'data-testid="pareto-content-layout"' in body
        assert '<div class="content-layout" data-testid="pareto-content-layout">' in body
        assert '<n-layout-content class="content-layout"' not in body
        assert 'data-testid="quality-controls"' in body
        assert "<h2>Quality weights</h2>" not in body
        assert "grid-template-columns: minmax(0, 1fr) 282px" in body
        assert ".content-layout { width: 100%;" in body
        assert ".chart-container { position: relative; width: 100%;" in body
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
        assert (
            "accuracy: 1,\n  'uptime-short': 0,\n  'uptime-long': 0,\n  'latency-short': 0,\n  'latency-long': 0,\n  'throughput-short': 0,\n  'throughput-long': 0,"
            in body
        )
        assert "function interpolateWeights(start, end, count)" in body
        assert "qualityWeights.accuracy = qualityControls.accuracy" in body
        assert "qualityWeights[key] = values[index]" in body
        assert "qualityWeights.uptime" not in body
        assert "const scheduleRenderChart = debounce((preserveViewport) => renderChart(preserveViewport), 100);" in body
        assert "function renderChart(preserveViewport = false)" in body
        assert "function preserveViewportForVisiblePoints(previousPoints, nextPoints, range)" in body
        assert "const previousPoints = preserveViewport ? renderedPoints : null;" in body
        assert "const visiblePointKeys = new Set" in body
        assert "let renderedPoints = [];" in body
        assert "renderedPoints = points;" in body
        assert "Math.min(...previousXValues) - range.xMin" in body
        assert "range.xMax - Math.max(...previousXValues)" in body
        assert (
            "const preservedRange = preserveViewportForVisiblePoints(previousPoints, points, previousViewRange);"
            in body
        )
        assert "viewRange.value = preservedRange || previousViewRange || { ...baseRange };" in body
        assert "const previousViewRange = preserveViewport ? { ...viewRange.value } : null;" in body
        assert "const yValues = points.map(point => point.value[1]);" in body
        assert "const yPadding = Math.max((yMaxValue - yMinValue) * 0.04, 0.01);" in body
        assert "xMin: xMinValue - xPadding" in body
        assert "yMin: yMinValue - yPadding" in body
        assert "xMin: Math.max(0, xMin" not in body
        assert "min: viewRange.value.xMin" in body
        assert "max: viewRange.value.xMax" in body
        assert "formatter: value => Number.isInteger(value) ? value : value.toFixed(1)" in body
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
