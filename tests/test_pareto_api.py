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
            "openrouter.env": "MCPTAP_API_KEY=sk-or-abcdef\n",
            "zenmux.env": "MCPTAP_API_KEY=sk-ai-...a8bd\n",
            "tokenrouter.env": "MCPTAP_API_KEY=\n",
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
    app = _configured_app(monkeypatch, tmp_path, {"openrouter.env": "MCPTAP_API_KEY=sk-....\n"})

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
            "proxy.env": 'MCPTAP_UPSTREAM_PROVIDER="openrouter"\nMCPTAP_LISTEN_PORT=8787\n',
            "openrouter.env": "MCPTAP_API_KEY=sk-or-abcdef\nMCPTAP_MODEL=old/model\nMCPTAP_PLAN_MODE_MODEL=old/plan\n",
            "zenmux.env": "MCPTAP_API_KEY=sk-zm-abcdef\nMCPTAP_MODEL=old/model\nMCPTAP_PLAN_MODE_MODEL=old/plan\n",
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
        assert "MCPTAP_MODEL=new/model:floor" in content
        assert "MCPTAP_PLAN_MODE_MODEL=old/plan" in content


@pytest.mark.asyncio
async def test_handle_provider_model_switches_upstream_provider(monkeypatch, tmp_path: Path):
    app = _provider_model_app(
        monkeypatch,
        tmp_path,
        {
            "proxy.env": 'MCPTAP_UPSTREAM_PROVIDER="openrouter"\n',
            "openrouter.env": "MCPTAP_API_KEY=sk-or-abcdef\nMCPTAP_MODEL=old/model\n",
            "zenmux.env": "MCPTAP_API_KEY=sk-zm-abcdef\nMCPTAP_MODEL=old/model\n",
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
        assert 'MCPTAP_UPSTREAM_PROVIDER="zenmux"' in proxy
        zenmux = (tmp_path / "mcptap" / "zenmux.env").read_text(encoding="utf-8")
        assert "MCPTAP_PLAN_MODE_MODEL=new/model" in zenmux
        untouched = (tmp_path / "mcptap" / "openrouter.env").read_text(encoding="utf-8")
        assert "MCPTAP_MODEL=old/model" in untouched


@pytest.mark.asyncio
async def test_handle_provider_model_appends_missing_key(monkeypatch, tmp_path: Path):
    app = _provider_model_app(
        monkeypatch,
        tmp_path,
        {
            "proxy.env": 'MCPTAP_UPSTREAM_PROVIDER="openrouter"\n',
            "openrouter.env": "MCPTAP_API_KEY=sk-or-abcdef\n",
        },
    )

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/provider-model",
            json={"provider": "openrouter", "alias": "new/model", "slot": "act"},
        )
        assert response.status == 200
        content = (tmp_path / "mcptap" / "openrouter.env").read_text(encoding="utf-8")
        assert "MCPTAP_MODEL=new/model" in content
        proxy = (tmp_path / "mcptap" / "proxy.env").read_text(encoding="utf-8")
        assert 'MCPTAP_UPSTREAM_PROVIDER="openrouter"' in proxy


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
    app = _provider_model_app(monkeypatch, tmp_path, {"openrouter.env": "MCPTAP_API_KEY=x\n"})

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/provider-model",
            json={"provider": "openrouter", "alias": "m/a", "slot": "turbo"},
        )
        assert response.status == 400


@pytest.mark.asyncio
async def test_handle_provider_model_rejects_empty_alias(monkeypatch, tmp_path: Path):
    app = _provider_model_app(monkeypatch, tmp_path, {"openrouter.env": "MCPTAP_API_KEY=x\n"})

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
        assert 'v-model:value="routerMinCostSliderPosition"' in body
        assert ':min="routerMinCostSliderMin"' in body
        assert ':max="routerMinCostSliderMax"' in body
        assert ':step="routerMinCostSliderStep"' in body
        assert ':format-tooltip="formatRouterMinCostTooltip"' in body
        assert "const routerMinCostSliderPosition = computed({" in body
        assert "const dollars = compressedCostAxis.value ? v * v : v;" in body
        assert "routerMinCostSliderMax = computed(() => displayXValue(5));" in body
        assert "formatTooltipThreeDecimals(routerMinCost)" in body
        assert "formatTooltipThreeDecimals(routerMaxCost)" in body
        assert "formatTooltipThreeDecimals(routerCostSplit)" in body
        assert "return `${formatTooltipThreeDecimals(dollars)} $`;" in body
        assert "function formatTooltipThreeDecimals(value)" in body
        assert "const s = value.toFixed(3);" in body
        assert "formatTooltipThreeDecimals,\n      routerMinCost" in body
        assert "function computeParetoCharacteristicPoints(linePoints, stripePercent)" in body
        assert (
            "const paretoActCharacteristicPoints = computeParetoCharacteristicPoints(paretoAct, routerParetoStripe.value);"
            in body
        )
        assert (
            "const paretoPlanCharacteristicPoints = computeParetoCharacteristicPoints(paretoPlan, routerParetoStripe.value);"
            in body
        )
        assert "const characteristicBorderWidth = isCharacteristic ? 5.04 : 2.52;" not in body
        assert "const characteristicRingSymbolSize = frontierSymbolSize * 2;" in body
        assert "borderWidth: frontierBorderWidth" in body
        assert "color: 'transparent'" in body
        assert "name: `${provider} characteristic`" not in body
        assert "name: '',\n          type: 'scatter',\n          symbolSize: characteristicRingSymbolSize" in body
        assert 'data-testid="router-slider-pareto-stripe"' in body
        assert "routerParetoStripe = ref(50)" in body
        assert ':min="10"' in body
        assert ':max="200"' in body
        assert ':max="150"' not in body
        assert "Math.min(200, v)" in body
        assert "computeParetoCharacteristicPoints(linePoints, stripePercent)" in body
        assert "const stripeLimit = computeParetoStripeLimit(startCost, stripePercent);" in body
        assert "function computeParetoStripeLimit(price, stripePercent)" in body
        assert "return price + (price + 1) * stripePercent / 100;" in body
        assert "const QUALITY_SLIDER_POSITIONS = {" in body
        assert "const QUALITY_SLIDER_MARKS = {" in body
        assert 'step="mark"' in body
        assert ".quality-slider .n-slider-marks { display: none; }" in body
        assert ':marks="control.marks"' in body
        assert ':marks="accuracySliderControl.marks"' in body
        assert ':marks="costSliderControl.marks"' in body
        assert "https://unpkg.com/naive-ui@2.45.3/dist/index.prod.js" in body
        assert "https://unpkg.com/vue@3.5.43/dist/vue.global.prod.js" in body
        assert "cost: [0, 0.25, 0.5, 0.75, 1]" in body
        assert "accuracy: [0.05, 0.25, 0.5, 0.75, 1]" in body
        assert "uptime: [0, 0.25, 0.5, 0.75, 1]" in body
        assert "latency: [0, 0.05, 0.25, 0.6, 1]" in body
        assert "throughput: [0, 0.05, 0.25, 0.6, 1]" in body
        assert ':max="QUALITY_SLIDER_POSITIONS.accuracy[QUALITY_SLIDER_POSITIONS.accuracy.length - 1]"' in body
        assert ':max="QUALITY_SLIDER_POSITIONS.cost[QUALITY_SLIDER_POSITIONS.cost.length - 1]"' in body
        assert ':value="qualityControls[control.key]"' in body
        assert "function nearestQualityValue(positions, value)" in body
        assert "function handleQualitySliderChange(control, value)" in body
        assert "function handleCostSliderChange(value)" in body
        assert ':step="0.05"' not in body
        assert ':value="qualityControls.accuracy"' in body
        assert ':value="costMix"' in body
        assert 'v-model:value="qualityControls[control.key]"' not in body
        assert "computeParetoCharacteristicPoints(paretoAct, routerParetoStripe.value)" in body
        assert "computeParetoCharacteristicPoints(paretoPlan, routerParetoStripe.value)" in body
        assert 'data-testid="pareto-characteristic-overlay"' not in body
        assert 'data-testid="pareto-characteristic-overlay-act"' not in body
        assert 'data-testid="pareto-characteristic-overlay-plan"' not in body
        assert "const CHARACTERISTIC_OVERLAY_WIDTH = 300;" in body
        assert "const CHARACTERISTIC_OVERLAY_WIDTH = 340;" not in body
        assert "const CHARACTERISTIC_OVERLAY_HEIGHT = 122;" in body
        assert "const CHARACTERISTIC_OVERLAY_HEIGHT = 130;" not in body
        assert "y: 12 + index * 18" in body
        assert "const CHARACTERISTIC_OVERLAY_HEIGHT = 196;" not in body
        assert "const CHARACTERISTIC_OVERLAY_TITLE_LIFT = 9;" in body
        assert "CHARACTERISTIC_OVERLAY_HEIGHT - CHARACTERISTIC_OVERLAY_TITLE_LIFT" in body
        assert "function overlayPointLines(point)" in body
        assert "Provider: {provider|" in body
        assert "Alias: {alias|" in body
        assert "provider: { font: '600 12px sans-serif', fill: '#fff' }" in body
        assert "alias: { font: '600 12px sans-serif', fill: '#fff' }" in body
        assert "const navigationControls = [" in body
        assert "if (count > 1) navigationControls.push(" in body
        assert "Weighted cost: {weightedCost|" in body
        assert "Quality: {quality|" in body
        assert "weightedCost: { font: '600 12px sans-serif', fill: '#fff' }" in body
        assert "quality: { font: '600 12px sans-serif', fill: '#fff' }" in body
        assert "function buildCharacteristicOverlayGraphic()" in body
        assert "zlevel: 20" in body
        assert "zlevel: 30" in body
        assert "zlevel: 2," not in body
        assert "zlevel: 3," not in body

        assert "graphic: buildCharacteristicOverlayGraphic()" in body
        assert "const navigationTop = CHARACTERISTIC_OVERLAY_HEIGHT - NAV_BUTTON_TOP_MARGIN;" in body
        assert "x: CHARACTERISTIC_OVERLAY_WIDTH - 91" not in body
        assert "x: CHARACTERISTIC_OVERLAY_WIDTH - 74" in body
        assert "text: 'PARETO'" in body
        assert "PARETO', fill: '#aab'" in body
        assert "y: navigationTop + 13" in body
        assert "...(point.isThinking ? ['Thinking: yes'] : [])," not in body
        assert "text: activeAct ? 'Act' : 'Plan'" not in body
        assert "function characteristicOverlayPointKey(point)" in body
        assert "return `${pointValueKey(point)},${point.provider},${point.alias},${point.name}`;" in body
        assert (
            "const currentPointKey = characteristicOverlayPoint.value ? characteristicOverlayPointKey(characteristicOverlayPoint.value) : null;"
            in body
        )
        assert (
            "characteristicOverlayIndex.value = nextPoints.findIndex(point => characteristicOverlayPointKey(point) === currentPointKey);"
            in body
        )
        assert "silent: false" not in body
        assert "function handleOverlayNavButton(action)" in body
        assert "onclick: () => { handleOverlayNavButton('prev'); }" not in body
        assert "onclick: () => { handleOverlayNavButton('next'); }" not in body
        assert "onclick: () => { handleOverlayNavButton('act'); }" not in body
        assert "onclick: () => { handleOverlayNavButton('plan'); }" not in body
        assert "cursor: 'pointer'" in body
        assert "cursor: 'default'" in body
        assert "silent: true,\n        cursor: 'default'," in body
        assert "const clientX = numeric(rawEvent?.clientX);" in body
        assert (
            "const chartX = clientX === null ? numeric(params?.zrX) ?? numeric(params?.offsetX) : clientX - chartRect.left;"
            in body
        )
        assert (
            "watch([characteristicOverlayLine, characteristicOverlayEffectiveLine, characteristicOverlayIndex]" in body
        )
        assert "silent: true," in body
        assert "NAV_BUTTON_TOP_MARGIN" in body
        assert "element onclick never fires inside a silent group" in body
        assert "const NAV_BUTTON_TOP_MARGIN = 34;" in body
        assert "const CHARACTERISTIC_OVERLAY_HIT_TARGET_TOP = 98;" not in body
        assert ".characteristic-overlay-hit-target" not in body
        assert "characteristicOverlayHitTargets" not in body
        assert "characteristicOverlayHitTargetsStyle" not in body
        assert 'data-testid="characteristic-overlay-hit-targets"' not in body
        assert "function handleCharacteristicOverlayClick(params)" in body
        assert "zr.on('click', handleCharacteristicOverlayClick);" in body
        assert "zr.off('click', handleCharacteristicOverlayClick);" in body
        assert "const top = CHARACTERISTIC_OVERLAY_HIT_TARGET_TOP;" not in body
        assert "navigationTop = CHARACTERISTIC_OVERLAY_HEIGHT - NAV_BUTTON_TOP_MARGIN" in body
        assert "replaceMerge: ['graphic']" in body
        assert "characteristicOverlayIndex.value = 0;" in body
        assert "stale index (and its" in body
        assert "renderChart(true);\n    }\n\n    function expandCharacteristicOverlayPoints" not in body
        assert "onclick: () => { handleOverlayNavButton('prev'); }" not in body
        assert "onclick: () => { handleOverlayNavButton('next'); }" not in body
        assert "onclick: () => { handleOverlayNavButton('act'); }" not in body
        assert "onclick: () => { handleOverlayNavButton('plan'); }" not in body
        assert "onclick: () => { characteristicOverlayPrev(); renderChart(true); }" not in body
        assert "onclick: () => { characteristicOverlayNext(); renderChart(true); }" not in body
        assert "onclick: () => { characteristicOverlayLine.value = 'act'; renderChart(true); }" not in body
        assert "onclick: () => { characteristicOverlayLine.value = 'plan'; renderChart(true); }" not in body
        assert "zr.off('click', handleCharacteristicOverlayClick);" in body
        assert "function buildClippedGridSeries()" in body
        assert "function niceTickStep(rawStep)" in body
        assert "if (!chart.value) return null;" in body
        assert "if (!chart.value || !characteristicOverlayPoint.value) return null;" not in body
        assert "characteristicOverlayGridClips().xAxis" not in body
        assert 'data-testid="pareto-clipped-grid"' not in body
        assert "const clippedGridSeries = buildClippedGridSeries();" in body
        assert "function refreshClippedGrid()" in body
        assert "id: 'characteristic-clipped-grid'," in body
        assert "refreshClippedGrid();" in body
        assert "setTimeout(() => renderChart(true), 0);" not in body
        assert "requestAnimationFrame(() => renderChart(true));" not in body
        assert "return segments.length ? {" in body
        assert "series.unshift(clippedGridSeries);" in body
        assert "splitLine: { show: false }" in body
        assert "legendHoverLink: false" in body
        assert "const prevProviderCount = ref(0);" in body
        assert "const prevModelCount = ref(0);" in body
        assert "const pendingQualityReset = ref(false);" in body
        assert "function handleFilterUpdate()" in body
        assert "const providerRemoved = providers.length < prevProviderCount.value;" in body
        assert "const modelRemoved = models.length < prevModelCount.value;" in body
        assert "pendingQualityReset.value = providerRemoved || modelRemoved;" in body
        assert "if (pendingQualityReset.value) {" in body
        assert "routerMinQuality.value = Math.max(routerQualityMinBound.value, 60);" in body
