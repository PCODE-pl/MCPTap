"""Hot-reload of configuration files at runtime.

Polls mtime of six configuration files and triggers a selective reload
cascade when any of them changes:

  proxy.env         -> reload env files + Settings + all dependent components
  openrouter.env    -> reload env files + Settings + all dependent components
  requesty.env      -> reload env files + Settings + all dependent components
  mcp-intercept.yaml -> reload MCPInterceptor (stop old subprocess, start new)
  per-model.yaml    -> reload per-model config dict
  use_tool_hook.py   -> reload tool hook enabled flag + Settings (path may change)

The reloader runs as a background asyncio task inside the aiohttp event loop.
It is designed for a development tool: simplicity over perfection.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from mcptap.mcp_intercept import MCPInterceptor, load_intercept_config
from mcptap.rewrite import load_per_model_config
from mcptap.settings import CONFIG_DIR, LOGGER, reload_settings, settings

# Poll interval (seconds).
_POLL_INTERVAL = 2.0

# Config file basenames relative to CONFIG_DIR.
_FILE_PROXY_ENV = "proxy.env"
_FILE_OPENROUTER_ENV = "openrouter.env"
_FILE_REQUESTY_ENV = "requesty.env"
_FILE_INTERCEPT_YAML = "mcp-intercept.yaml"
_FILE_PER_MODEL_YAML = "per-model.yaml"
_FILE_USE_TOOL_HOOK = "use_tool_hook.py"

# Files whose change triggers a full env + Settings reload.
_ENV_FILES = {_FILE_PROXY_ENV, _FILE_OPENROUTER_ENV, _FILE_REQUESTY_ENV}

# Files whose content is embedded in settings (path stored in proxy.env).
_SETTING_BACKED_FILES = {_FILE_INTERCEPT_YAML, _FILE_PER_MODEL_YAML, _FILE_USE_TOOL_HOOK}


class ConfigReloader:
    """Polls config files and triggers reload callbacks on change.

    Lifecycle: ``start()`` launches a background task; ``stop()`` cancels it.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None
        self._mtimes: Dict[str, float] = {}
        self._app: Optional[Any] = None

        # Callbacks set by the application during wiring.
        self._on_env_reload: Optional[Callable[[], Awaitable[None]]] = None
        self._on_intercept_reload: Optional[Callable[[], Awaitable[None]]] = None
        self._on_per_model_reload: Optional[Callable[[], Awaitable[None]]] = None
        self._on_tool_hook_reload: Optional[Callable[[], Awaitable[None]]] = None

    def attach(
        self,
        app: Any,
        on_env_reload: Callable[[], Awaitable[None]],
        on_intercept_reload: Callable[[], Awaitable[None]],
        on_per_model_reload: Callable[[], Awaitable[None]],
        on_tool_hook_reload: Callable[[], Awaitable[None]],
    ) -> None:
        """Wire the reloader to application lifecycle callbacks."""
        self._app = app
        self._on_env_reload = on_env_reload
        self._on_intercept_reload = on_intercept_reload
        self._on_per_model_reload = on_per_model_reload
        self._on_tool_hook_reload = on_tool_hook_reload

    def start(self) -> None:
        """Launch the background polling task."""
        if self._task is not None:
            return
        self._init_mtimes()
        self._task = asyncio.ensure_future(self._poll_loop())
        LOGGER.info("ConfigReloader started (poll interval=%.1fs)", _POLL_INTERVAL)

    async def stop(self) -> None:
        """Cancel the polling task and wait for it to finish."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        LOGGER.info("ConfigReloader stopped")

    # ------------------------------------------------------------------
    # Internal: file tracking
    # ------------------------------------------------------------------

    def _config_path(self, filename: str) -> Path:
        return Path(CONFIG_DIR) / filename

    def _file_exists(self, filename: str) -> bool:
        return self._config_path(filename).is_file()

    def _get_mtime(self, filename: str) -> Optional[float]:
        try:
            return os.path.getmtime(self._config_path(filename))
        except OSError:
            return None

    def _init_mtimes(self) -> None:
        """Snapshot current mtimes of all watched files."""
        for fname in self._all_watched_files():
            self._mtimes[fname] = self._get_mtime(fname) or 0.0

    def _all_watched_files(self) -> Set[str]:
        return _ENV_FILES | _SETTING_BACKED_FILES

    def _detect_changes(self) -> Set[str]:
        """Return the set of filenames whose mtime increased since the last check."""
        changed: Set[str] = set()
        for fname in self._all_watched_files():
            current = self._get_mtime(fname)
            if current is None:
                continue
            if current > self._mtimes.get(fname, 0.0):
                changed.add(fname)
                self._mtimes[fname] = current
        return changed

    # ------------------------------------------------------------------
    # Internal: reload cascade
    # ------------------------------------------------------------------

    async def _handle_changes(self, changed: Set[str]) -> None:
        """Run the selective reload cascade for the given changed files."""
        if not changed:
            return

        LOGGER.info("ConfigReloader: detected changes: %s", ", ".join(sorted(changed)))

        # Step 1: If any env file changed, delegate to the env reload callback
        # which handles the full cascade (env -> Settings -> all components).
        env_changed = changed & _ENV_FILES
        if env_changed:
            await self._safe_call(self._on_env_reload, "env reload")
            # env reload callback handles the full cascade, so we're done.
            return

        # Step 2: Handle direct file changes (not via settings).
        if _FILE_INTERCEPT_YAML in changed:
            await self._safe_call(self._on_intercept_reload, "intercept reload")
        if _FILE_PER_MODEL_YAML in changed:
            await self._safe_call(self._on_per_model_reload, "per-model reload")
        if _FILE_USE_TOOL_HOOK in changed:
            await self._safe_call(self._on_tool_hook_reload, "tool-hook reload")

    async def _safe_call(
        self,
        callback: Optional[Callable[[], Awaitable[None]]],
        label: str,
    ) -> None:
        """Call an async callback, logging errors instead of propagating."""
        if callback is None:
            return
        try:
            await callback()
        except Exception as exc:
            LOGGER.error("ConfigReloader: %s failed: %s", label, exc)

    # ------------------------------------------------------------------
    # Internal: polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Main polling loop — runs until cancelled."""
        while True:
            try:
                changed = self._detect_changes()
                if changed:
                    await self._handle_changes(changed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.error("ConfigReloader: poll loop error: %s", exc)
            await asyncio.sleep(_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Application-level callback factories
# ---------------------------------------------------------------------------


async def reload_per_model_config(app: Any) -> None:
    """Reload per-model config and update app["per_model_config"]."""
    try:
        new_config = load_per_model_config()
        app["per_model_config"] = new_config
        LOGGER.info("Per-model config reloaded: %d entries", len(new_config))
    except Exception as exc:
        LOGGER.error("Per-model config reload failed: %s", exc)


async def reload_tool_hook(app: Any) -> None:
    """Update ToolHookGateway.enabled to match current settings."""
    hook_gateway = app.get("hook_gateway")
    if hook_gateway is None:
        return
    new_enabled = bool(settings.use_tool_hook)
    if hook_gateway.enabled != new_enabled:
        LOGGER.info("Tool hook %s", "enabled" if new_enabled else "disabled")
    hook_gateway.enabled = new_enabled


async def reload_intercept(app: Any) -> None:
    """Restart the MCPInterceptor with the latest config.

    The old interceptor is stopped first, then a new one is created and
    started. If the new config is invalid or the MCP server fails to start,
    the old interceptor is kept running.
    """
    old_intercept: Optional[MCPInterceptor] = app.get("mcp_intercept")
    if old_intercept is not None:
        await old_intercept.stop()

    try:
        new_config = load_intercept_config()
    except Exception as exc:
        LOGGER.error("Intercept config reload failed, disabling intercept: %s", exc)
        new_config = None

    new_intercept = MCPInterceptor(new_config)
    app["mcp_intercept"] = new_intercept

    if new_intercept.enabled:
        try:
            await new_intercept.start()
            LOGGER.info("MCP interceptor reloaded and started")
        except Exception as exc:
            LOGGER.error("MCP interceptor start failed after reload: %s", exc)


async def reload_env_and_propagate(app: Any) -> None:
    """Reload env files + Settings, then propagate to all dependent components.

    This is the full cascade: env -> Settings -> per-model / tool-hook / intercept.
    """
    try:
        reload_settings()
    except Exception as exc:
        LOGGER.error("Env reload failed, keeping previous settings: %s", exc)
        return

    await reload_per_model_config(app)
    await reload_tool_hook(app)
    await reload_intercept(app)
