"""Tests for the Pareto data HTTP handlers."""

from pathlib import Path
from unittest import mock

import pytest  # type: ignore
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from mcptap.pareto_api import (
    handle_configured_providers,
    handle_pareto_data,
    handle_pareto_refresh,
    handle_pareto_tested_data,
    handle_provider_model,
    serve_pareto_page,
)


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
async def test_handle_pareto_tested_data_returns_json_from_configured_path(tmp_path: Path):
    target = tmp_path / "tested_models.json"
    target.write_text('{"providers": ["openrouter"], "free": {}, "paid": {}}', encoding="utf-8")
    app = web.Application()
    app["pareto_tested_path"] = target
    app.router.add_get("/api/pareto-tested", handle_pareto_tested_data)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/pareto-tested")
        assert response.status == 200
        assert await response.json() == {"providers": ["openrouter"], "free": {}, "paid": {}}


@pytest.mark.asyncio
async def test_handle_pareto_tested_data_returns_not_found_when_file_is_missing(tmp_path: Path):
    app = web.Application()
    app["pareto_tested_path"] = tmp_path / "missing.json"
    app.router.add_get("/api/pareto-tested", handle_pareto_tested_data)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/pareto-tested")
        assert response.status == 404
        assert await response.json() == {"error": "Tested models data not found"}


@pytest.mark.asyncio
async def test_handle_pareto_tested_data_returns_503_for_invalid_json(tmp_path: Path):
    target = tmp_path / "tested_models.json"
    target.write_text("not json", encoding="utf-8")
    app = web.Application()
    app["pareto_tested_path"] = target
    app.router.add_get("/api/pareto-tested", handle_pareto_tested_data)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/pareto-tested")
        assert response.status == 503
        assert await response.json() == {"error": "Tested models data is unavailable"}


@pytest.mark.asyncio
async def test_handle_pareto_tested_data_returns_503_for_non_object_payload(tmp_path: Path):
    target = tmp_path / "tested_models.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    app = web.Application()
    app["pareto_tested_path"] = target
    app.router.add_get("/api/pareto-tested", handle_pareto_tested_data)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/pareto-tested")
        assert response.status == 503
        assert await response.json() == {"error": "Tested models data is unavailable"}


@pytest.mark.asyncio
async def test_handle_pareto_refresh_refetches_github_and_serves_fresh_payload(tmp_path: Path):
    pareto_task = mock.Mock()
    tested_task = mock.Mock()
    pareto_task.refresh_now = mock.AsyncMock(return_value=None)
    tested_task.refresh_now = mock.AsyncMock(return_value=None)
    target = tmp_path / "pareto.json"
    tested_target = tmp_path / "tested_models.json"
    target.write_text('{"fresh": "pareto"}', encoding="utf-8")
    tested_target.write_text('{"fresh": "tested"}', encoding="utf-8")
    app = web.Application()
    app["pareto_data"] = pareto_task
    app["pareto_tested_data"] = tested_task
    app["pareto_path"] = target
    app["pareto_tested_path"] = tested_target
    app.router.add_post("/api/pareto-refresh", handle_pareto_refresh)

    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/pareto-refresh")
        pareto_task.refresh_now.assert_awaited_once()
        tested_task.refresh_now.assert_awaited_once()
        assert response.status == 200
        body = await response.json()
        assert body["refreshed"] is True
        assert body["pareto"] == {"fresh": "pareto"}
        assert body["tested"] == {"fresh": "tested"}


@pytest.mark.asyncio
async def test_handle_pareto_refresh_returns_503_without_tasks(tmp_path: Path):
    app = web.Application()
    app.router.add_post("/api/pareto-refresh", handle_pareto_refresh)

    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/pareto-refresh")
        assert response.status == 503


@pytest.mark.asyncio
async def test_handle_pareto_refresh_returns_502_when_github_fetch_fails(tmp_path: Path):
    pareto_task = mock.Mock()
    tested_task = mock.Mock()
    pareto_task.refresh_now = mock.AsyncMock(side_effect=RuntimeError("HTTP 500"))
    tested_task.refresh_now = mock.AsyncMock(return_value=None)
    app = web.Application()
    app["pareto_data"] = pareto_task
    app["pareto_tested_data"] = tested_task
    app["pareto_path"] = tmp_path / "pareto.json"
    app["pareto_tested_path"] = tmp_path / "tested_models.json"
    app.router.add_post("/api/pareto-refresh", handle_pareto_refresh)

    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/pareto-refresh")
        assert response.status == 502
        assert "HTTP 500" in (await response.json())["error"]


def _configured_app(monkeypatch, tmp_path: Path, files: dict[str, str]) -> web.Application:
    from mcptap import pareto_api

    config_dir = tmp_path / "mcptap"
    config_dir.mkdir(exist_ok=True)
    for name, content in files.items():
        (config_dir / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(pareto_api, "CONFIG_DIR", config_dir)
    app = web.Application()
    app.router.add_get("/api/configured-providers", handle_configured_providers)
    return app


@pytest.mark.asyncio
async def test_handle_configured_providers_lists_real_keys_only(monkeypatch, tmp_path: Path):
    app = _configured_app(
        monkeypatch,
        tmp_path,
        {
            "openrouter.env": "MCP_TAP_API_KEY=sk-or-abcdef\n",
            "zenmux.env": "MCP_TAP_API_KEY=sk-ai-...a8bd\n",
            "tokenrouter.env": "MCP_TAP_API_KEY=\n",
        },
    )

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/configured-providers")
        assert response.status == 200
        body = await response.json()
        assert "openrouter" in body["providers"]
        assert "zenmux" not in body["providers"]
        assert "tokenrouter" not in body["providers"]


@pytest.mark.asyncio
async def test_handle_configured_providers_empty_when_no_real_keys(monkeypatch, tmp_path: Path):
    app = _configured_app(monkeypatch, tmp_path, {"openrouter.env": "MCP_TAP_API_KEY=sk-....\n"})

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/configured-providers")
        assert response.status == 200
        assert (await response.json()) == {"providers": []}


def _provider_model_app(monkeypatch, tmp_path: Path, files: dict[str, str]) -> web.Application:
    from mcptap import pareto_api

    config_dir = tmp_path / "mcptap"
    config_dir.mkdir(exist_ok=True)
    for name, content in files.items():
        (config_dir / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(pareto_api, "CONFIG_DIR", config_dir)
    app = web.Application()
    app.router.add_post("/api/provider-model", handle_provider_model)
    return app


@pytest.mark.asyncio
async def test_handle_provider_model_sets_act_model(monkeypatch, tmp_path: Path):
    app = _provider_model_app(
        monkeypatch,
        tmp_path,
        {
            "proxy.env": 'MCP_TAP_UPSTREAM_PROVIDER="openrouter"\nMCP_TAP_LISTEN_PORT=8787\n',
            "openrouter.env": "MCP_TAP_API_KEY=sk-or-abcdef\nMCP_TAP_MODEL=old/model\nMCP_TAP_PLAN_MODE_MODEL=old/plan\n",
            "zenmux.env": "MCP_TAP_API_KEY=sk-zm-abcdef\nMCP_TAP_MODEL=old/model\nMCP_TAP_PLAN_MODE_MODEL=old/plan\n",
        },
    )

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/provider-model",
            json={"provider": "openrouter", "alias": "new/model:floor", "slot": "act"},
        )
        assert response.status == 200
        body = await response.json()
        assert body == {
            "provider": "openrouter",
            "slot": "act",
            "model": "new/model:floor",
            "upstream_provider": "openrouter",
        }
        content = (tmp_path / "mcptap" / "openrouter.env").read_text(encoding="utf-8")
        assert "MCP_TAP_MODEL=new/model:floor" in content
        assert "MCP_TAP_PLAN_MODE_MODEL=old/plan" in content


@pytest.mark.asyncio
async def test_handle_provider_model_switches_upstream_provider(monkeypatch, tmp_path: Path):
    app = _provider_model_app(
        monkeypatch,
        tmp_path,
        {
            "proxy.env": 'MCP_TAP_UPSTREAM_PROVIDER="openrouter"\n',
            "openrouter.env": "MCP_TAP_API_KEY=sk-or-abcdef\nMCP_TAP_MODEL=old/model\n",
            "zenmux.env": "MCP_TAP_API_KEY=sk-zm-abcdef\nMCP_TAP_MODEL=old/model\n",
        },
    )

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/provider-model",
            json={"provider": "zenmux", "alias": "new/model", "slot": "plan"},
        )
        assert response.status == 200
        body = await response.json()
        assert body["upstream_provider"] == "zenmux"
        proxy = (tmp_path / "mcptap" / "proxy.env").read_text(encoding="utf-8")
        assert 'MCP_TAP_UPSTREAM_PROVIDER="zenmux"' in proxy
        zenmux = (tmp_path / "mcptap" / "zenmux.env").read_text(encoding="utf-8")
        assert "MCP_TAP_PLAN_MODE_MODEL=new/model" in zenmux
        untouched = (tmp_path / "mcptap" / "openrouter.env").read_text(encoding="utf-8")
        assert "MCP_TAP_MODEL=old/model" in untouched


@pytest.mark.asyncio
async def test_handle_provider_model_appends_missing_key(monkeypatch, tmp_path: Path):
    app = _provider_model_app(
        monkeypatch,
        tmp_path,
        {
            "proxy.env": 'MCP_TAP_UPSTREAM_PROVIDER="openrouter"\n',
            "openrouter.env": "MCP_TAP_API_KEY=sk-or-abcdef\n",
        },
    )

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/provider-model",
            json={"provider": "openrouter", "alias": "new/model", "slot": "act"},
        )
        assert response.status == 200
        content = (tmp_path / "mcptap" / "openrouter.env").read_text(encoding="utf-8")
        assert "MCP_TAP_MODEL=new/model" in content
        proxy = (tmp_path / "mcptap" / "proxy.env").read_text(encoding="utf-8")
        assert 'MCP_TAP_UPSTREAM_PROVIDER="openrouter"' in proxy


@pytest.mark.asyncio
async def test_handle_provider_model_rejects_unknown_provider(monkeypatch, tmp_path: Path):
    app = _provider_model_app(monkeypatch, tmp_path, {})

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/provider-model",
            json={"provider": "nonexistent", "alias": "m/a", "slot": "act"},
        )
        assert response.status == 400


@pytest.mark.asyncio
async def test_handle_provider_model_rejects_bad_slot(monkeypatch, tmp_path: Path):
    app = _provider_model_app(monkeypatch, tmp_path, {"openrouter.env": "MCP_TAP_API_KEY=x\n"})

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/provider-model",
            json={"provider": "openrouter", "alias": "m/a", "slot": "turbo"},
        )
        assert response.status == 400


@pytest.mark.asyncio
async def test_handle_provider_model_rejects_empty_alias(monkeypatch, tmp_path: Path):
    app = _provider_model_app(monkeypatch, tmp_path, {"openrouter.env": "MCP_TAP_API_KEY=x\n"})

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/provider-model",
            json={"provider": "openrouter", "alias": "  ", "slot": "act"},
        )
        assert response.status == 400


@pytest.mark.asyncio
async def test_serve_pareto_page_returns_html():
    app = web.Application()
    app.router.add_get("/ui/pareto", serve_pareto_page)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/ui/pareto")
        assert response.status == 200
        body = await response.text()
        assert "MCPTap Pareto" in body
        assert "https://cdn.jsdelivr.net/npm/echarts@6.1.0/dist/echarts.min.js" in body
        assert "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js" not in body
        assert "weightedCost" in body
        assert "fmtSig(point.weightedCost)" in body
        assert "fmtSig(point.value[0])" not in body
        assert "pareto-popup" in body
        assert "max-height: calc(100vh - 24px)" in body
        assert "getBoundingClientRect" in body
        assert "zoomOnMouseWheel" not in body
        assert "getZr()" in body
        assert "convertFromPixel" in body
        assert "wheelDelta" in body
        assert "function zoomRangeAroundAnchor(anchor, currentRange, factor, limits)" in body
        assert "const anchorRatio = Math.min(1, Math.max(0, (anchor - currentRange.min) / currentWidth));" in body
        assert "const targetWidth = currentWidth * factor;" in body
        assert "if (limits.forceMin !== null && nextMin > limits.forceMin && anchorRatio > 0) {" in body
        assert "nextMax = nextMin + (anchor - nextMin) / anchorRatio;" in body
        assert "const clampToBase = factor > 1;" in body
        assert "if (clampToBase && limits.min !== null && nextMin < limits.min)" in body
        assert "if (clampToBase && limits.max !== null && nextMax > limits.max)" in body
        assert "const anchor = chart.value.convertFromPixel({ xAxisIndex: 0, yAxisIndex: 0 }, pixel);" in body
        assert "const internalX = numeric(params.zrX) ?? numeric(event.zrX);" in body
        assert "if (internalX !== null && internalY !== null) return [internalX, internalY];" in body
        assert "const nextYRange = zoomRangeAroundAnchor(anchor[1], {" in body
        assert "yMin: nextYRange.min" in body
        assert "yMax: nextYRange.max" in body
        assert "const nextXRange = zoomRangeAroundAnchor(anchor[0], {" in body
        assert "}, factor, {" in body
        assert "xMin: nextXRange.min" in body
        assert "xMax: nextXRange.max" in body
        assert "const nextXRange = zoomRangeAroundAnchor(anchor[0], {" in body
        assert "forceMin: null," in body
        assert "zeroPriceXMin" not in body
        assert "const providerModelCounts = new Map();" in body
        assert "providerModelCounts.get(point.provider).add(point.name);" in body
        assert "const providers = [...providerModelCounts.keys()].sort((left, right) => {" in body
        assert "providerModelCounts.get(right).size - providerModelCounts.get(left).size" in body
        assert "return countDifference || left.localeCompare(right);" in body
        assert "let legendVisibility = {};" in body
        assert "function handleLegendSelectChanged(params)" in body
        assert "legendVisibility = { ...params.selected };" in body
        assert "const legendSelection = providers.reduce(" in body
        assert "[provider]: legendVisibility[provider] !== false" in body
        assert "chart.value.on('legendselectchanged', handleLegendSelectChanged);" in body
        assert "chart.value.off('legendselectchanged', handleLegendSelectChanged);" in body

        assert "notMerge: true" in body
        assert 'data-testid="pareto-page"' in body
        assert 'data-testid="pareto-content-layout"' in body
        assert '<div class="content-layout" data-testid="pareto-content-layout">' in body
        assert '<n-layout-content class="content-layout"' not in body
        assert 'data-testid="controls"' in body
        assert 'data-testid="quality"' in body
        assert 'data-testid="router"' in body
        assert "<h2>Quality weights</h2>" not in body
        assert "grid-template-columns: minmax(0, 1fr) 340px" in body
        assert (
            ".toolbar-filters { display: flex; align-items: center; gap: 12px; flex: 0 0 60%; min-width: 0; flex-direction: column; align-items: stretch; }"
            in body
        )
        assert (
            ".toolbar-actions { display: flex; align-items: center; gap: 12px; flex: 0 0 20%; flex-wrap: wrap; min-width: 0; }"
            in body
        )
        assert '.toolbar-actions[data-testid="toolbar-secondary-actions"] { flex: 1 1 20%; }' in body
        assert 'data-testid="toolbar-filters"' in body
        assert 'data-testid="toolbar-actions"' in body
        assert 'data-testid="toolbar-secondary-actions"' in body
        assert body.index('data-testid="toolbar-actions"') < body.index("include-untested-wrapper")
        assert 'data-testid="only-configured-wrapper"' in body
        assert 'data-testid="only-configured-checkbox"' in body
        assert "<span>only configured providers</span>" in body
        assert "const onlyConfigured = ref(true);" in body
        assert "const configuredProviders = ref([]);" in body
        assert "function handleOnlyConfiguredChange(checked)" in body
        assert "if (onlyConfigured.value) includeUntested.value = false;" in body
        assert ':disabled="onlyConfigured"' in body
        assert "if (onlyConfigured.value && !configuredProviders.value.includes(provider)) continue;" in body
        assert "fetch('/api/configured-providers', { cache: 'no-store' })" in body
        assert "configuredProviders.value = Array.isArray(configured.providers)" in body
        assert ".content-layout { width: 100%;" in body
        assert ".chart-container { position: relative; width: 100%;" in body
        assert ':data-testid="`quality-slider-${control.key}`"' in body
        assert "const DEFAULT_COST_MIX = 0.5;" in body
        assert "const costMix = ref(DEFAULT_COST_MIX);" in body
        assert "if (costMix.value <= 0) return 'In';" in body
        assert "if (costMix.value >= 1) return 'Out';" in body
        assert "return 'In/Out';" in body
        assert 'data-testid="quality-slider-cost-mix-output"' in body
        assert "{{ costMix.toFixed(2) }}" not in body
        assert "function handleCostMixChange(value)" in body
        assert "costMix.value = numeric(value) ?? DEFAULT_COST_MIX;" in body
        assert "const weightedCost = (1 - costMix.value) * offer.input + costMix.value * offer.output;" in body
        assert 'data-testid="quality-slider-cost-mix"' in body
        assert 'data-testid="quality-slider-row-primary"' in body
        assert 'for="quality-slider-cost-mix"' in body
        assert "singleSliderDefinitions" not in body
        assert "pairedSliderDefinitions = {" in body
        assert "control.key.startsWith('uptime-')" in body
        assert "control.key.startsWith('latency-')" in body
        assert "control.key.startsWith('throughput-')" in body
        assert 'data-testid="quality-slider-row-uptime"' in body
        assert 'data-testid="quality-slider-row-latency"' in body
        assert 'data-testid="quality-slider-row-throughput"' in body
        assert (
            ".quality-slider-row { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; margin-bottom: 14px; }"
            in body
        )
        assert "const QUALITY_SLIDER_DEFINITIONS = [" in body
        assert "{ key: 'accuracy', label: 'Accuracy', min: 0.05 }," in body
        assert "{ key: 'uptime-short', label: 'Uptime short' }" in body
        assert "{ key: 'uptime-long', label: 'Uptime long' }" in body
        assert "{ key: 'latency-short', label: 'Latency short' }" in body
        assert "{ key: 'latency-long', label: 'Latency long' }" in body
        assert "{ key: 'throughput-short', label: 'Speed short' }" in body
        assert "{ key: 'throughput-long', label: 'Speed long' }" in body
        assert "const QUALITY_SLIDER_MAX = 1;" in body
        assert "{ key: 'accuracy', label: 'Accuracy', min: 0.05 }," in body
        assert ':max="control.max ?? QUALITY_SLIDER_MAX"' in body
        assert "QUALITY_SLIDER_ACCURACY_MAX" not in body
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
        assert "const previousViewRange = preserveViewport ? { ...viewRange.value } : null;" in body
        assert "const yValues = points.map(point => point.value[1]);" in body
        assert "const yPadding = Math.max((yMaxValue - yMinValue) * 0.04, 0.01);" in body
        assert "xMin: Math.max(0, xMinValue - xPadding)" in body

        assert "yMin: Math.max(0, yMinValue - yPadding)" in body
        assert "const boundedRange = {" in body
        assert "xMin: Math.max(0, range.xMin)" in body
        assert "yMin: Math.max(baseRange.yMin, range.yMin)" in body
        assert "viewRange.value = { ...boundedRange };" in body
        assert "const nextViewRange = preservedRange || previousViewRange || { ...baseRange };" in body
        assert "yMin: Math.max(baseRange.yMin, nextViewRange.yMin)" in body
        assert "min: viewRange.value.xMin" in body
        assert "max: viewRange.value.xMax" in body
        assert (
            "axisLabel: { color: '#aaa', formatter: value => Number.isInteger(value) ? value : value.toFixed(1) }"
            in body
        )
        assert "formatter: value => Number.isInteger(value) ? value : value.toFixed(1)" in body
        assert "<h1" not in body
        assert 'data-testid="pareto-chart"' in body
        assert 'data-testid="pareto-zoom-area"' not in body
        assert "const areaZoomActive = ref(true);" in body
        assert "areaZoomActive.value = false" not in body
        assert 'data-testid="pareto-zoom-reset"' not in body
        assert "resetZoom" not in body
        assert "chart-controls" not in body
        assert 'data-testid="pareto-save-image"' not in body
        assert "saveChartImage" not in body
        assert 'role="toolbar"' not in body
        assert 'aria-label="Pareto scatter chart showing weighted cost and quality"' in body
        assert "new URLSearchParams(window.location.search).get('e2llm') === '2'" in body
        assert "const hasProviderFilter = selectedProviders.value.length > 0;" in body
        assert "if (hasProviderFilter && !selectedProviders.value.includes(provider)) continue;" in body
        assert "const selectedProviders = ref([]);" in body
        assert "DEFAULT_PROVIDERS" not in body
        assert "const includeUntested = ref(false);" in body
        assert 'v-model:checked="includeUntested"' in body
        assert "handleUntestedChange" in body
        assert "const testedKeys = includeUntested.value ? null : buildTestedOfferKeys(testedData);" in body
        assert "const testedData = rawTested.value;" in body
        assert "for (const section of ['free', 'paid']) {" in body
        assert "fetch('/api/pareto-refresh', { method: 'POST', cache: 'no-store' })" in body
        assert "rawData.value = refreshed.pareto;" in body
        assert "rawTested.value = refreshed.tested" in body
        assert "testedProviders" not in body
        assert "keys.add(`${canonicalModel} ${provider}`);" in body
        assert "keys.add(`${canonicalModel} ${provider} ${alias}`);" in body
        assert (
            "if (testedKeys && !providerAliases.some(alias => testedKeys.has(`${canonicalModel} ${provider} ${alias}`))) continue;"
            in body
        )
        assert "if (testedKeys && !testedKeys.has(`${canonicalModel} ${provider} ${alias}`)) continue;" in body
        assert 'data-testid="sqrt-cost-checkbox"' in body
        assert 'aria-label="Square-root cost scale"' in body
        assert "<span>scale</span>" in body
        assert "sqrt scale" not in body
        assert "const compressedCostAxis = ref(true);" in body
        assert "function handleCostScaleChange(checked)" in body
        assert "updateScaleOverlay" in body
        assert "return Math.sqrt(Math.max(value, 0));" in body
        assert "return compressedCostAxis.value ? transformXPoint(value) : value;" in body
        assert "value => formatCompressedAxisLabel(value)" in body
        assert "name: 'Weighted cost ($/M tokens)', nameLocation: 'middle', nameGap: 38," in body
        assert "'Weighted cost ($/M tokens, sqrt)'" not in body
        assert "'Weighted cost ($/M tokens, log)'" not in body
        assert "grid: { left: 72, right: 32, top: 52, bottom: 66 }" in body
        assert "const plotWidth = chartWidth - 72 - 32;" in body
        assert "requestAnimationFrame(() => updateScaleOverlay());" in body
        assert "chartRect.height - 66 + 31;" in body
        assert "if (value === 0) return '0';" in body
        assert "logCostAxis" not in body
        assert "LOG_X" not in body
        assert "type: 'log'" not in body
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
        assert "tooltip: { show: false }" in body
        assert "chart.value.on('mouseover'" in body
        assert "chart.value.on('mouseout'" in body
        assert "pareto-popup" in body
        assert 'data-testid="pareto-popup"' in body
        assert 'data-testid="pareto-popup-nav"' in body
        assert "popupVisible" in body
        assert "popupPoints" in body
        assert "popupIndex" in body
        assert "findOverlappingPoints" in body
        assert "handlePopupEnter" in body
        assert "handlePopupLeave" in body
        assert "popupNext" in body
        assert "popupPrev" in body
        assert "POPUP_OVERLAP_PX" in body
        assert 'data-testid="pareto-popup-actions"' in body
        assert 'data-testid="pareto-act-model"' in body
        assert 'data-testid="pareto-plan-model"' in body
        assert 'data-testid="pareto-toast"' in body
        assert "pareto-popup-notice" not in body
        assert ':disabled="!popupActionable"' in body
        assert ':title="popupActionTitle"' in body
        assert '@click="handleActModel"' in body
        assert '@click="handlePlanModel"' in body
        assert "const popupActionReason = computed(() => {" in body
        assert "if (!configuredProviders.value.includes(point.provider))" in body
        assert "buildTestedOfferKeys(rawTested.value)" in body
        assert "if (!tested.has(`${point.name} ${point.provider} ${point.alias}`))" in body
        assert "fetch('/api/provider-model'" in body
        assert "toastMessage" in body
        assert ".pareto-toast { position: fixed;" in body
        assert "setTimeout(() => { toastMessage.value = ''; }, 5000)" in body
