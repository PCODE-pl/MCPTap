"""Application configuration loaded from environment and .env files.

Settings are loaded at import time and can be hot-reloaded at runtime via
``reload_settings()``. The module-level ``settings`` object is a proxy
that delegates attribute access to the current Settings instance, so all
callers automatically see the latest values after a reload.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set

from dotenv import load_dotenv  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROVIDER_OPENROUTER = "openrouter"
PROVIDER_REQUESTY = "requesty"

SYNTHETIC_GET_GOAL_CALL_ID = "synthetic_get_goal"
SYNTHETIC_GET_GOAL_TOOL_NAME = "get_goal"

SENSITIVE_HEADER_NAMES: Set[str] = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
}

HOP_BY_HOP_HEADERS: Set[str] = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

CONFIG_DIR = Path.home() / ".config/mcptap"

# Keys that ``load_dotenv`` injects from provider env files. These must be
# cleaned from ``os.environ`` before loading a different provider file to avoid
# stale values leaking across provider switches.
_PROVIDER_ENV_KEYS = [
    "MCP_TAP_API_KEY",
    "MCP_TAP_MODEL",
    "MCP_TAP_PLAN_MODE_MODEL",
    "MCP_TAP_OPENROUTER_PROVIDER",
    "MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS",
]


@dataclass
class Settings:
    """Immutable configuration loaded from environment and .env files."""

    # Network
    listen_host: str
    listen_port: int

    # Upstream provider
    upstream_provider: str
    upstream_base_url: str
    provider_env_file: str
    api_key: str

    # Model forcing
    model: str
    plan_mode_model: str
    plan_mode_trigger: str
    plan_mode_max_input_size: int

    # OpenRouter provider pinning
    openrouter_provider: str
    openrouter_disable_provider_fallbacks: bool

    # MCP intercept
    intercept_yaml: str
    intercept_max_iterations: int
    intercept_tool_timeout: float

    # Per-model instructions
    per_model_yaml: str

    # Logging
    log_level: str
    log_file: str
    log_file_redact_headers: bool
    log_payload_keys: List[str]

    # Tool-call hook
    use_tool_hook: str
    use_tool_hook_timeout: float
    use_tool_hook_synthetic_tool: str
    use_tool_hook_pending_ttl: float

    # Per-session blocklist directory
    per_session_dir: str

    # Request log database
    log_db_path: str


class _SettingsProxy:
    """Transparent proxy that delegates attribute access to the current Settings.

    ``from mcptap.settings import settings`` binds to this proxy object.
    When ``reload_settings()`` swaps the internal instance, all existing
    references immediately see the new values without re-importing.
    """

    __slots__ = ("_target",)

    def __init__(self, target: Settings) -> None:
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "_target":
            object.__setattr__(self, name, value)
        else:
            setattr(self._target, name, value)

    def __repr__(self) -> str:
        return f"_SettingsProxy({self._target!r})"

    def _swap(self, target: Settings) -> None:
        """Replace the proxied Settings instance (internal use)."""
        object.__setattr__(self, "_target", target)


def _load_env_files() -> None:
    """Load proxy.env and the provider-specific env file into os.environ.

    Stale provider keys are removed before loading a new provider file to
    prevent values from one provider leaking into another.
    """
    load_dotenv(CONFIG_DIR / "proxy.env", override=True)

    upstream_provider = os.environ.get("MCP_TAP_UPSTREAM_PROVIDER") or ""

    provider_env_file = ""
    if PROVIDER_OPENROUTER == upstream_provider:
        provider_env_file = "openrouter.env"
    elif PROVIDER_REQUESTY == upstream_provider:
        provider_env_file = "requesty.env"
    if not provider_env_file:
        raise RuntimeError("MCP_TAP_UPSTREAM_PROVIDER must be one of 'openrouter' or 'requesty'")

    # Remove stale provider-specific keys before loading the new provider file.
    for key in _PROVIDER_ENV_KEYS:
        os.environ.pop(key, None)

    load_dotenv(CONFIG_DIR / provider_env_file, override=True)


def _build_settings() -> Settings:
    """Build a Settings instance from the current os.environ."""
    upstream_provider = os.environ.get("MCP_TAP_UPSTREAM_PROVIDER") or ""

    upstream_base_url = ""
    provider_env_file = ""
    if PROVIDER_OPENROUTER == upstream_provider:
        upstream_base_url = "https://openrouter.ai/api/v1"
        provider_env_file = "openrouter.env"
    elif PROVIDER_REQUESTY == upstream_provider:
        upstream_base_url = "https://router.requesty.ai/v1"
        provider_env_file = "requesty.env"
    if not upstream_base_url:
        raise RuntimeError("MCP_TAP_UPSTREAM_PROVIDER must be one of 'openrouter' or 'requesty'")

    api_key = (os.environ.get("MCP_TAP_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("MCP_TAP_API_KEY must not be empty")

    model = (os.environ.get("MCP_TAP_MODEL") or "").strip()
    plan_mode_model = (os.environ.get("MCP_TAP_PLAN_MODE_MODEL") or "").strip()
    if not model or not plan_mode_model:
        raise RuntimeError("MCP_TAP_MODEL and MCP_TAP_PLAN_MODE_MODEL must not be empty")

    # Requesty model name normalization
    if PROVIDER_REQUESTY == upstream_provider:
        if model.startswith("openai") and "-responses" not in model:
            vendor, mdl = model.split("/", 1)
            model = f"{vendor}-responses/{mdl}"
        if plan_mode_model.startswith("openai") and "-responses" not in plan_mode_model:
            vendor, mdl = plan_mode_model.split("/", 1)
            plan_mode_model = f"{vendor}-responses/{mdl}"
        model = model.split(":")[0]
        plan_mode_model = plan_mode_model.split(":")[0]

    openrouter_provider = (os.environ.get("MCP_TAP_OPENROUTER_PROVIDER") or "").strip()
    openrouter_disable_provider_fallbacks = os.environ.get(
        "MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS", "1"
    ).lower() not in {"0", "false", "no", "off"}

    _synthetic_tool_env = os.environ.get("MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL")
    use_tool_hook_synthetic_tool = _synthetic_tool_env.strip() if _synthetic_tool_env is not None else "get_goal"

    return Settings(
        listen_host=os.environ.get("MCP_TAP_LISTEN_HOST", "127.0.0.1"),
        listen_port=int(os.environ.get("MCP_TAP_LISTEN_PORT", "8787")),
        upstream_provider=upstream_provider,
        upstream_base_url=upstream_base_url,
        provider_env_file=provider_env_file,
        api_key=api_key,
        model=model,
        plan_mode_model=plan_mode_model,
        plan_mode_trigger=(os.environ.get("MCP_TAP_PLAN_MODE_TRIGGER") or "max").strip(),
        plan_mode_max_input_size=int(os.environ.get("MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE", 100000)),
        openrouter_provider=openrouter_provider,
        openrouter_disable_provider_fallbacks=openrouter_disable_provider_fallbacks,
        intercept_yaml=(os.environ.get("MCP_TAP_INTERCEPT_YAML") or "").strip(),
        intercept_max_iterations=int(os.environ.get("MCP_TAP_INTERCEPT_MAX_ITERATIONS", "8")),
        intercept_tool_timeout=float(os.environ.get("MCP_TAP_INTERCEPT_TOOL_TIMEOUT", "120")),
        per_model_yaml=(os.environ.get("MCP_TAP_PER_MODEL_YAML") or "").strip(),
        log_level=(os.environ.get("MCP_TAP_LOG_LEVEL") or "INFO").upper(),
        log_file=(os.environ.get("MCP_TAP_LOG_FILE") or "").strip(),
        log_file_redact_headers=(
            os.environ.get("LOG_FILE_REDACT_HEADERS", "0").lower() not in {"0", "false", "no", "off"}
        ),
        log_payload_keys=["tools"],
        use_tool_hook=(os.environ.get("MCP_TAP_USE_TOOL_HOOK") or "").strip(),
        use_tool_hook_timeout=float(os.environ.get("MCP_TAP_USE_TOOL_HOOK_TIMEOUT", "30")),
        use_tool_hook_synthetic_tool=use_tool_hook_synthetic_tool,
        use_tool_hook_pending_ttl=float(os.environ.get("MCP_TAP_USE_TOOL_HOOK_PENDING_TTL", "600")),
        per_session_dir=(os.environ.get("MCP_TAP_PER_SESSION_DIR") or "/tmp/mcptap/per_session").strip(),
        log_db_path=(os.environ.get("MCP_TAP_LOG_DB") or os.path.expanduser("~/.local/share/mcptap/logs.db")).strip(),
    )


def _setup_logging(s: Settings) -> logging.Logger:
    """Configure root and communication loggers based on settings."""
    logging.basicConfig(
        level=getattr(logging, s.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger("mcptap")
    logger.setLevel(getattr(logging, s.log_level, logging.INFO))

    comm_logger = logging.getLogger("mcptap-communication")
    comm_logger.propagate = False
    comm_logger.setLevel(logging.INFO)
    if s.log_file:
        handler = logging.FileHandler(s.log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        comm_logger.addHandler(handler)

    return logger


def reload_settings() -> Settings:
    """Reload env files, rebuild Settings, swap the proxy target, reconfigure logging.

    Returns the new Settings instance.
    """
    _load_env_files()
    new_settings = _build_settings()
    settings._swap(new_settings)
    _setup_logging(new_settings)
    LOGGER.info("Settings reloaded: provider=%s model=%s", new_settings.upstream_provider, new_settings.model)
    return new_settings


# ---------------------------------------------------------------------------
# Module-level initialization
# ---------------------------------------------------------------------------

# Load env files into os.environ on first import.
_load_env_files()

# Proxy created once at import time. reload_settings() swaps the internal
# target so all callers see the new values.
settings: Settings = _SettingsProxy(_build_settings())  # type: ignore
LOGGER = _setup_logging(settings)
COMMUNICATION_LOGGER = logging.getLogger("mcptap-communication")

# Debug payload keys for logging
DEBUG_PAYLOAD_KEYS: List[str] = ["tools"]
