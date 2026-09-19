"""HTTP handlers for the Pareto chart and its data source."""

import json
from pathlib import Path

from aiohttp import web  # type: ignore

from mcptap.settings import LOGGER

_DEFAULT_PARETO_PATH = Path(__file__).resolve().parent.parent / "data" / "pareto.json"
_DEFAULT_TESTED_MODELS_PATH = Path(__file__).resolve().parent.parent / "data" / "tested_models.json"
CONFIG_DIR = Path.home() / ".config/mcptap"

_PROVIDER_MODEL_KEYS = {"act": "MCPTAP_MODEL", "plan": "MCPTAP_PLAN_MODE_MODEL"}


def _provider_env_file(provider: str) -> Path | None:
    name = provider.strip().lower()
    if not name or not all(ch.isalnum() or ch in "-_" for ch in name):
        return None
    path = CONFIG_DIR / f"{name}.env"
    if path.name != f"{name}.env" or not path.is_file():
        return None
    return path


def _set_env_key(path: Path, key: str, value: str, quoted: bool = False) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Cannot read {path.name}: {exc}") from exc
    rendered = f'{key}="{value}"' if quoted else f"{key}={value}"
    lines = content.splitlines()
    updated = False
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[index] = rendered
            updated = True
            break
    if not updated:
        lines.append(rendered)
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Cannot write {path.name}: {exc}") from exc


def _set_provider_model(path: Path, key: str, alias: str) -> None:
    _set_env_key(path, key, alias)


async def handle_provider_model(request: web.Request) -> web.Response:
    """Set the provider model slot and switch MCPTAP_UPSTREAM_PROVIDER to it.

    Writes MCPTAP_MODEL (act) or MCPTAP_PLAN_MODE_MODEL (plan) into the
    provider .env file and points proxy.env at that provider. The
    ConfigReloader picks both files up live.
    """
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "Request body must be JSON"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "Request body must be a JSON object"}, status=400)
    provider = str(payload.get("provider", ""))
    alias = str(payload.get("alias", "")).strip()
    slot = str(payload.get("slot", "")).strip().lower()
    if slot not in _PROVIDER_MODEL_KEYS:
        return web.json_response({"error": "slot must be 'act' or 'plan'"}, status=400)
    if not alias or "\n" in alias or "\r" in alias:
        return web.json_response({"error": "alias must be a non-empty single line"}, status=400)
    path = _provider_env_file(provider)
    if path is None:
        return web.json_response({"error": f"Unknown provider: {provider}"}, status=400)
    try:
        _set_provider_model(path, _PROVIDER_MODEL_KEYS[slot], alias)
        _set_env_key(CONFIG_DIR / "proxy.env", "MCPTAP_UPSTREAM_PROVIDER", path.stem, quoted=True)
    except RuntimeError as exc:
        LOGGER.error("Failed to set provider model: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)
    LOGGER.info("Provider model set: provider=%s slot=%s model=%s", path.stem, slot, alias)
    return web.json_response({"provider": path.stem, "slot": slot, "model": alias, "upstream_provider": path.stem})


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
            if stripped.startswith("MCPTAP_API_KEY="):
                key = stripped.split("=", 1)[1].strip()
                break
        if key and "..." not in key:
            providers.append(provider_file.stem)
    return providers


async def handle_configured_providers(_request: web.Request) -> web.Response:
    """Return providers with a real (non-placeholder) MCPTAP_API_KEY."""
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
