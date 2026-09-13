"""HTTP handlers for the Pareto chart and its data source."""

import json
from pathlib import Path

from aiohttp import web  # type: ignore

from mcptap.settings import LOGGER

_DEFAULT_PARETO_PATH = Path(__file__).resolve().parent.parent / "data" / "pareto.json"
_DEFAULT_TESTED_MODELS_PATH = Path(__file__).resolve().parent.parent / "data" / "tested_models.json"
CONFIG_DIR = Path.home() / ".config/mcptap"


def _configured_providers() -> list[str]:
    providers: list[str] = []
    for provider_file in sorted(CONFIG_DIR.glob("*.env")):
        if provider_file.name == "proxy.env":
            continue
        try:
            content = provider_file.read_text(encoding="utf-8")
        except OSError:
            continue
        key = ""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("MCP_TAP_API_KEY="):
                key = stripped.split("=", 1)[1].strip()
                break
        if key and "..." not in key:
            providers.append(provider_file.stem)
    return providers


async def handle_configured_providers(_request: web.Request) -> web.Response:
    """Return providers with a real (non-placeholder) MCP_TAP_API_KEY."""
    return web.json_response({"providers": _configured_providers()})


async def handle_pareto_refresh(request: web.Request) -> web.Response:
    """Refetch both artifacts from GitHub, then return the fresh local payloads."""
    pareto_task = request.app.get("pareto_data")
    tested_task = request.app.get("pareto_tested_data")
    if pareto_task is None or tested_task is None:
        return web.json_response({"error": "Pareto refresh is unavailable"}, status=503)
    try:
        await pareto_task.refresh_now()
    except Exception as exc:
        LOGGER.error("Pareto refresh failed: %s", exc)
        return web.json_response({"error": f"Pareto refresh failed: {exc}"}, status=502)
    try:
        await tested_task.refresh_now()
    except Exception as exc:
        LOGGER.error("Tested models refresh failed: %s", exc)
        return web.json_response({"error": f"Tested models refresh failed: {exc}"}, status=502)
    pareto_payload = _read_payload(_pareto_path(request))
    if pareto_payload is None:
        return web.json_response({"error": "Pareto data is unavailable"}, status=503)
    tested_payload = _read_payload(_pareto_tested_path(request))
    if tested_payload is None:
        return web.json_response({"error": "Tested models data is unavailable"}, status=503)
    return web.json_response({"refreshed": True, "pareto": pareto_payload, "tested": tested_payload})


def _read_payload(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as pareto_file:
            payload = json.load(pareto_file)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        LOGGER.error("Failed to read Pareto data from %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        LOGGER.error("Pareto data at %s is not a JSON object", path)
        return None
    return payload


def _pareto_path(request: web.Request) -> Path:
    return Path(request.app.get("pareto_path", _DEFAULT_PARETO_PATH))


def _pareto_tested_path(request: web.Request) -> Path:
    return Path(request.app.get("pareto_tested_path", _DEFAULT_TESTED_MODELS_PATH))


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


async def handle_pareto_tested_data(request: web.Request) -> web.Response:
    """Return the latest locally stored tested-models JSON."""
    path = _pareto_tested_path(request)
    try:
        with path.open("r", encoding="utf-8") as tested_file:
            payload = json.load(tested_file)
    except FileNotFoundError:
        return web.json_response({"error": "Tested models data not found"}, status=404)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.error("Failed to read tested models data from %s: %s", path, exc)
        return web.json_response({"error": "Tested models data is unavailable"}, status=503)
    if not isinstance(payload, dict):
        LOGGER.error("Tested models data at %s is not a JSON object", path)
        return web.json_response({"error": "Tested models data is unavailable"}, status=503)
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
