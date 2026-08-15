"""OpenRouter tau-bench data provider and Pareto frontier calculation."""

from __future__ import annotations

from numbers import Real
from typing import Any, Dict, Iterable, List, Optional

from aiohttp import ClientError, ClientSession, ClientTimeout  # type: ignore

from mcptap.settings import settings

BENCHMARK_ENDPOINT = (
    "https://openrouter.ai/api/v1/benchmarks?source=openrouter&benchmark_type=tau_bench_verified_airline"
)


class OpenRouterBenchmarkError(RuntimeError):
    """Raised when the OpenRouter benchmark cannot be fetched or decoded."""


def _metric_value(record: Dict[str, Any], field: str) -> Optional[float]:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    return float(value)


def calculate_pareto_frontier(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return records that are not dominated by another record.

    Accuracy is maximized and average cost per task is minimized. Records without
    numeric values for either metric are ignored because they cannot be placed on
    the frontier.
    """
    candidates = [
        record
        for record in records
        if _metric_value(record, "accuracy") is not None and _metric_value(record, "avg_cost_per_task") is not None
    ]

    frontier: List[Dict[str, Any]] = []
    for candidate in candidates:
        candidate_accuracy = _metric_value(candidate, "accuracy")
        candidate_cost = _metric_value(candidate, "avg_cost_per_task")
        assert candidate_accuracy is not None
        assert candidate_cost is not None

        dominated = False
        for other in candidates:
            if other is candidate:
                continue
            other_accuracy = _metric_value(other, "accuracy")
            other_cost = _metric_value(other, "avg_cost_per_task")
            assert other_accuracy is not None
            assert other_cost is not None

            no_worse = other_accuracy >= candidate_accuracy and other_cost <= candidate_cost
            strictly_better = other_accuracy > candidate_accuracy or other_cost < candidate_cost
            if no_worse and strictly_better:
                dominated = True
                break

        if not dominated:
            frontier.append(candidate)

    def sort_key(record: Dict[str, Any]) -> tuple[float, float]:
        cost = _metric_value(record, "avg_cost_per_task")
        accuracy = _metric_value(record, "accuracy")
        assert cost is not None
        assert accuracy is not None
        return cost, -accuracy

    return sorted(frontier, key=sort_key)


class OpenRouterBenchmarkProvider:
    """Fetch tau-bench results from OpenRouter and calculate their frontier."""

    def __init__(
        self,
        endpoint: str = BENCHMARK_ENDPOINT,
        api_key: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._timeout = ClientTimeout(total=timeout_seconds)

    def _get_api_key(self) -> str:
        api_key = self._api_key if self._api_key is not None else settings.api_key
        api_key = api_key.strip()
        if not api_key:
            raise OpenRouterBenchmarkError("MCP_TAP_API_KEY must not be empty")
        return api_key

    async def fetch_benchmark(self) -> Dict[str, Any]:
        """Fetch and return the complete OpenRouter benchmark response."""
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._get_api_key()}",
        }

        try:
            async with ClientSession(timeout=self._timeout) as session:
                async with session.get(self._endpoint, headers=headers) as response:
                    if response.status >= 400:
                        body = await response.text()
                        raise OpenRouterBenchmarkError(
                            f"OpenRouter benchmark request failed with HTTP {response.status}: {body[:500]}"
                        )
                    payload = await response.json()
        except OpenRouterBenchmarkError:
            raise
        except (ClientError, ValueError) as exc:
            raise OpenRouterBenchmarkError(f"OpenRouter benchmark request could not be completed: {exc}") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise OpenRouterBenchmarkError("OpenRouter benchmark response has no data list")
        return payload

    async def fetch_pareto(self) -> List[Dict[str, Any]]:
        """Fetch benchmark results and return only the Pareto frontier."""
        payload = await self.fetch_benchmark()
        return calculate_pareto_frontier(payload["data"])
