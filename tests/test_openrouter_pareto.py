"""Tests for the OpenRouter tau-bench Pareto provider."""

import os
import sys

import pytest  # type: ignore
from aiohttp import web  # type: ignore
from aiohttp.test_utils import TestServer  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcptap.pareto.openrouter import (  # noqa: E402
    OpenRouterBenchmarkProvider,
    calculate_pareto_frontier,
)


@pytest.mark.parametrize(
    "records, expected_slugs",
    [
        (
            [
                {"model_permaslug": "cheap", "accuracy": 0.70, "avg_cost_per_task": 0.01},
                {"model_permaslug": "balanced", "accuracy": 0.80, "avg_cost_per_task": 0.02},
                {"model_permaslug": "dominated", "accuracy": 0.75, "avg_cost_per_task": 0.03},
            ],
            ["cheap", "balanced"],
        ),
        (
            [
                {"model_permaslug": "first", "accuracy": 0.80, "avg_cost_per_task": 0.02},
                {"model_permaslug": "second", "accuracy": 0.80, "avg_cost_per_task": 0.02},
            ],
            ["first", "second"],
        ),
    ],
)
def test_calculate_pareto_frontier_keeps_non_dominated_records(records, expected_slugs):
    result = calculate_pareto_frontier(records)

    assert [record["model_permaslug"] for record in result] == expected_slugs


@pytest.mark.asyncio
async def test_fetch_benchmark_uses_configured_token_and_endpoint(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        return web.json_response(
            {
                "data": [
                    {
                        "model_permaslug": "openai/test-model",
                        "accuracy": 0.8,
                        "avg_cost_per_task": 0.02,
                    }
                ],
                "meta": {"source": "openrouter"},
            }
        )

    app = web.Application()
    app.router.add_get("/benchmarks", handler)

    async with TestServer(app) as server:
        monkeypatch.setattr(
            "mcptap.pareto.openrouter.settings.api_key",
            "configured-openrouter-token",
        )
        provider = OpenRouterBenchmarkProvider(
            endpoint=f"{server.make_url('/benchmarks')}?source=openrouter&benchmark_type=tau_bench_verified_airline"
        )

        response = await provider.fetch_benchmark()

    assert response["meta"] == {"source": "openrouter"}
    assert requests[0].headers["Authorization"] == "Bearer configured-openrouter-token"
    assert requests[0].query["source"] == "openrouter"
    assert requests[0].query["benchmark_type"] == "tau_bench_verified_airline"


@pytest.mark.asyncio
async def test_fetch_pareto_returns_filtered_benchmark_records(monkeypatch):
    async def handler(_request):
        return web.json_response(
            {
                "data": [
                    {
                        "model_permaslug": "cheap",
                        "accuracy": 0.70,
                        "avg_cost_per_task": 0.01,
                    },
                    {
                        "model_permaslug": "balanced",
                        "accuracy": 0.80,
                        "avg_cost_per_task": 0.02,
                    },
                    {
                        "model_permaslug": "dominated",
                        "accuracy": 0.75,
                        "avg_cost_per_task": 0.03,
                    },
                ]
            }
        )

    app = web.Application()
    app.router.add_get("/benchmarks", handler)

    async with TestServer(app) as server:
        monkeypatch.setattr("mcptap.pareto.openrouter.settings.api_key", "test-token")
        provider = OpenRouterBenchmarkProvider(endpoint=str(server.make_url("/benchmarks")))

        result = await provider.fetch_pareto()

    assert [record["model_permaslug"] for record in result] == ["cheap", "balanced"]
