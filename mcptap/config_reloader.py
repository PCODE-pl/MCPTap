"""Hot-reload of configuration files at runtime.

Polls mtime of nine configuration files and triggers a selective reload
cascade when any of them changes:

  proxy.env         -> reload env files + Settings + all dependent components
  openrouter.env    -> reload env files + Settings + all dependent components
  requesty.env      -> reload env files + Settings + all dependent components
  meta.env          -> reload env files + Settings + all dependent components
  nano-gpt.env      -> reload env files + Settings + all dependent components
  llmtr.env         -> reload env files + Settings + all dependent components
  [...].env         -> reload env files + Settings + all dependent components
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
_FILE_META_ENV = "meta.env"
_FILE_NANO_GPT_ENV = "nano-gpt.env"
_FILE_LLMTR_ENV = "llmtr.env"
_FILE_KENARI_ENV = "kenari.env"
_FILE_UNOROUTER_ENV = "unorouter.env"
_FILE_RUNINFRA_ENV = "runinfra.env"
_FILE_NVIDIA_ENV = "nvidia.env"
_FILE_OPENCODE_ENV = "opencode.env"
_FILE_ORCAROUTER_ENV = "orcarouter.env"
_FILE_AIHUBMIX_ENV = "aihubmix.env"
_FILE_TOKENROUTER_ENV = "tokenrouter.env"
_FILE_INFERX_ENV = "inferx.env"
_FILE_KILO_ENV = "kilo.env"
_FILE_PENDRA_ENV = "pendra.env"
_FILE_VERCEL_ENV = "vercel.env"
_FILE_ZENMUX_ENV = "zenmux.env"
_FILE_ABLITERATION_AI_ENV = "abliteration-ai.env"
_FILE_ABOVE_ENV = "above.env"
_FILE_AGENTROUTER_ENV = "agentrouter.env"
_FILE_AGNES_ENV = "agnes.env"
_FILE_AI_ROUTER_ENV = "ai-router.env"
_FILE_AIAND_ENV = "aiand.env"
_FILE_AINETCAFE_ENV = "ainetcafe.env"
_FILE_AIXY_ENV = "aixy.env"
_FILE_AKI_IO_ENV = "aki-io.env"
_FILE_ALIBABA_ENV = "alibaba.env"
_FILE_302AI_ENV = "302ai.env"
_FILE_ABACUS_ENV = "abacus.env"
_FILE_ALIBABA_CN_ENV = "alibaba-cn.env"
_FILE_ALIBABA_CODING_PLAN_ENV = "alibaba-coding-plan.env"
_FILE_ALIBABA_CODING_PLAN_CN_ENV = "alibaba-coding-plan-cn.env"
_FILE_ALIBABA_TOKEN_PLAN_ENV = "alibaba-token-plan.env"
_FILE_ALIBABA_TOKEN_PLAN_CN_ENV = "alibaba-token-plan-cn.env"
_FILE_AMAZON_BEDROCK_ENV = "amazon-bedrock.env"
_FILE_AMBIENT_ENV = "ambient.env"
_FILE_AMD_ENV = "amd.env"
_FILE_ANTHROPIC_ENV = "anthropic.env"
_FILE_ANYAPI_ENV = "anyapi.env"
_FILE_ARCEE_ENV = "arcee.env"
_FILE_ATOMIC_CHAT_ENV = "atomic-chat.env"
_FILE_AURIKO_ENV = "auriko.env"
_FILE_AZURE_ENV = "azure.env"
_FILE_AZURE_COGNITIVE_SERVICES_ENV = "azure-cognitive-services.env"
_FILE_BAILING_ENV = "bailing.env"
_FILE_BASETEN_ENV = "baseten.env"
_FILE_BERGET_ENV = "berget.env"
_FILE_BLUECLAW_ENV = "blueclaw.env"
_FILE_BOTHUB_ENV = "bothub.env"
_FILE_CEREBRAS_ENV = "cerebras.env"
_FILE_CHUTES_ENV = "chutes.env"
_FILE_CLARIFAI_ENV = "clarifai.env"
_FILE_CLAUDINIO_ENV = "claudinio.env"
_FILE_CLINE_PASS_ENV = "cline-pass.env"
_FILE_CLOUDFERRO_SHERLOCK_ENV = "cloudferro-sherlock.env"
_FILE_CLOUDFLARE_AI_GATEWAY_ENV = "cloudflare-ai-gateway.env"
_FILE_CLOUDFLARE_WORKERS_AI_ENV = "cloudflare-workers-ai.env"
_FILE_COHERE_ENV = "cohere.env"
_FILE_CORALBRICKS_ENV = "coralbricks.env"
_FILE_CORTECS_ENV = "cortecs.env"
_FILE_CROF_ENV = "crof.env"
_FILE_CROSSMODEL_ENV = "crossmodel.env"
_FILE_CRUSOE_ENV = "crusoe.env"
_FILE_DAOXE_ENV = "daoxe.env"
_FILE_DATABRICKS_ENV = "databricks.env"
_FILE_DEEPINFRA_ENV = "deepinfra.env"
_FILE_DEEPSEEK_ENV = "deepseek.env"
_FILE_DIGITALOCEAN_ENV = "digitalocean.env"
_FILE_DINFERENCE_ENV = "dinference.env"
_FILE_DRUN_ENV = "drun.env"
_FILE_EBCLOUD_ENV = "ebcloud.env"
_FILE_ECHO_ENV = "echo.env"
_FILE_EDENAI_ENV = "edenai.env"
_FILE_EMPIRIOLABS_ENV = "empiriolabs.env"
_FILE_EVROC_ENV = "evroc.env"
_FILE_FASTROUTER_ENV = "fastrouter.env"
_FILE_FIREWORKS_AI_ENV = "fireworks-ai.env"
_FILE_FREEMODEL_ENV = "freemodel.env"
_FILE_FRIENDLI_ENV = "friendli.env"
_FILE_FROGBOT_ENV = "frogbot.env"
_FILE_GITHUB_COPILOT_ENV = "github-copilot.env"
_FILE_GMICLOUD_ENV = "gmicloud.env"
_FILE_GOOGLE_ENV = "google.env"
_FILE_GOOGLE_VERTEX_ENV = "google-vertex.env"
_FILE_GOOGLE_VERTEX_ANTHROPIC_ENV = "google-vertex-anthropic.env"
_FILE_GREENPT_ENV = "greenpt.env"
_FILE_GROQ_ENV = "groq.env"
_FILE_INTERCEPT_YAML = "mcp-intercept.yaml"
_FILE_PER_MODEL_YAML = "per-model.yaml"
_FILE_USE_TOOL_HOOK = "use_tool_hook.py"

# Files whose change triggers a full env + Settings reload.
_ENV_FILES = {
    _FILE_PROXY_ENV,
    _FILE_OPENROUTER_ENV,
    _FILE_REQUESTY_ENV,
    _FILE_META_ENV,
    _FILE_NANO_GPT_ENV,
    _FILE_LLMTR_ENV,
    _FILE_KENARI_ENV,
    _FILE_UNOROUTER_ENV,
    _FILE_RUNINFRA_ENV,
    _FILE_NVIDIA_ENV,
    _FILE_OPENCODE_ENV,
    _FILE_ORCAROUTER_ENV,
    _FILE_AIHUBMIX_ENV,
    _FILE_TOKENROUTER_ENV,
    _FILE_INFERX_ENV,
    _FILE_KILO_ENV,
    _FILE_PENDRA_ENV,
    _FILE_VERCEL_ENV,
    _FILE_ZENMUX_ENV,
    _FILE_ABLITERATION_AI_ENV,
    _FILE_ABOVE_ENV,
    _FILE_AGENTROUTER_ENV,
    _FILE_AGNES_ENV,
    _FILE_AI_ROUTER_ENV,
    _FILE_AIAND_ENV,
    _FILE_AINETCAFE_ENV,
    _FILE_AIXY_ENV,
    _FILE_AKI_IO_ENV,
    _FILE_ALIBABA_ENV,
    _FILE_302AI_ENV,
    _FILE_ABACUS_ENV,
    _FILE_ALIBABA_CN_ENV,
    _FILE_ALIBABA_CODING_PLAN_ENV,
    _FILE_ALIBABA_CODING_PLAN_CN_ENV,
    _FILE_ALIBABA_TOKEN_PLAN_ENV,
    _FILE_ALIBABA_TOKEN_PLAN_CN_ENV,
    _FILE_AMAZON_BEDROCK_ENV,
    _FILE_AMBIENT_ENV,
    _FILE_AMD_ENV,
    _FILE_ANTHROPIC_ENV,
    _FILE_ANYAPI_ENV,
    _FILE_ARCEE_ENV,
    _FILE_ATOMIC_CHAT_ENV,
    _FILE_AURIKO_ENV,
    _FILE_AZURE_ENV,
    _FILE_AZURE_COGNITIVE_SERVICES_ENV,
    _FILE_BAILING_ENV,
    _FILE_BASETEN_ENV,
    _FILE_BERGET_ENV,
    _FILE_BLUECLAW_ENV,
    _FILE_BOTHUB_ENV,
    _FILE_CEREBRAS_ENV,
    _FILE_CHUTES_ENV,
    _FILE_CLARIFAI_ENV,
    _FILE_CLAUDINIO_ENV,
    _FILE_CLINE_PASS_ENV,
    _FILE_CLOUDFERRO_SHERLOCK_ENV,
    _FILE_CLOUDFLARE_AI_GATEWAY_ENV,
    _FILE_CLOUDFLARE_WORKERS_AI_ENV,
    _FILE_COHERE_ENV,
    _FILE_CORALBRICKS_ENV,
    _FILE_CORTECS_ENV,
    _FILE_CROF_ENV,
    _FILE_CROSSMODEL_ENV,
    _FILE_CRUSOE_ENV,
    _FILE_DAOXE_ENV,
    _FILE_DATABRICKS_ENV,
    _FILE_DEEPINFRA_ENV,
    _FILE_DEEPSEEK_ENV,
    _FILE_DIGITALOCEAN_ENV,
    _FILE_DINFERENCE_ENV,
    _FILE_DRUN_ENV,
    _FILE_EBCLOUD_ENV,
    _FILE_ECHO_ENV,
    _FILE_EDENAI_ENV,
    _FILE_EMPIRIOLABS_ENV,
    _FILE_EVROC_ENV,
    _FILE_FASTROUTER_ENV,
    _FILE_FIREWORKS_AI_ENV,
    _FILE_FREEMODEL_ENV,
    _FILE_FRIENDLI_ENV,
    _FILE_FROGBOT_ENV,
    _FILE_GITHUB_COPILOT_ENV,
    _FILE_GMICLOUD_ENV,
    _FILE_GOOGLE_ENV,
    _FILE_GOOGLE_VERTEX_ENV,
    _FILE_GOOGLE_VERTEX_ANTHROPIC_ENV,
    _FILE_GREENPT_ENV,
    _FILE_GROQ_ENV,
}

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
    credits_checker = app.get("credits_checker")

    try:
        reload_settings()
    except Exception as exc:
        LOGGER.error("Env reload failed, keeping previous settings: %s", exc)
        return

    await reload_per_model_config(app)
    await reload_tool_hook(app)
    await reload_intercept(app)

    # Notify credits checker that the provider may have changed.
    if credits_checker is not None:
        credits_checker.on_provider_changed()
