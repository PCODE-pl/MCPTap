"""HTTP handlers for the Pareto chart and its data source."""

import json
from pathlib import Path

from aiohttp import web  # type: ignore

from mcptap.settings import LOGGER

_DEFAULT_PARETO_PATH = Path(__file__).resolve().parent.parent / "data" / "pareto.json"


def _pareto_path(request: web.Request) -> Path:
    return Path(request.app.get("pareto_path", _DEFAULT_PARETO_PATH))


async def handle_pareto_data(request: web.Request) -> web.Response:
    """Return the latest locally stored Pareto JSON."""
    path = _pareto_path(request)
    try:
        with path.open("r", encoding="utf-8") as pareto_file:
            payload = json.load(pareto_file)
    except FileNotFoundError:
        return web.json_response({"error": "Pareto data not found"}, status=404)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.error("Failed to read Pareto data from %s: %s", path, exc)
        return web.json_response({"error": "Pareto data is unavailable"}, status=503)
    if not isinstance(payload, dict):
        LOGGER.error("Pareto data at %s is not a JSON object", path)
        return web.json_response({"error": "Pareto data is unavailable"}, status=503)
    return web.json_response(payload)


async def serve_pareto_page(_request: web.Request) -> web.Response:
    """Serve the Pareto chart HTML page."""
    html_path = Path(__file__).resolve().parent / "static" / "pareto.html"
    try:
        html_content = html_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        LOGGER.error("Pareto viewer HTML not found at %s", html_path)
        return web.Response(text="Pareto viewer not found", status=404)
    return web.Response(text=html_content, content_type="text/html")
