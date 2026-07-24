"""Tests for hot-reload of configuration files.

Covers:
- _SettingsProxy delegation and swap semantics
- reload_settings() reloads env files and swaps the proxy target
- ConfigReloader mtime detection and selective reload cascade
- Application-level reload callbacks (per-model, tool-hook, intercept)
- Stale env key cleanup on provider switch
"""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from mcptap.config_reloader import (  # noqa: E402
    ConfigReloader,
    reload_env_and_propagate,
    reload_intercept,
    reload_per_model_config,
    reload_tool_hook,
)
from mcptap.settings import (  # noqa: E402
    Settings,
    _load_env_files,
    _SettingsProxy,
)
from mcptap.settings import (  # noqa: E402
    settings as global_settings,
)

# ---------------------------------------------------------------------------
# Helper: create a Settings instance for tests
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    defaults = dict(
        listen_host="127.0.0.1",
        listen_port=8787,
        upstream_provider="openrouter",
        upstream_base_url="https://openrouter.ai/api/v1",
        provider_env_file="openrouter.env",
        api_key="key",
        model="m",
        plan_mode_model="pm",
        plan_mode_trigger="max",
        plan_mode_max_input_size=100000,
        openrouter_provider="",
        openrouter_disable_provider_fallbacks=True,
        intercept_yaml="",
        intercept_max_iterations=8,
        intercept_tool_timeout=120.0,
        per_model_yaml="",
        log_level="INFO",
        log_file="",
        log_file_redact_headers=False,
        log_payload_keys=["tools"],
        use_tool_hook="",
        use_tool_hook_timeout=30.0,
        use_tool_hook_synthetic_tool="get_goal",
        use_tool_hook_pending_ttl=600.0,
        per_session_dir="/tmp/x",
        log_db_path="/tmp/x.db",
    )
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# _SettingsProxy tests
# ---------------------------------------------------------------------------


class TestSettingsProxy:
    def test_proxy_delegates_attribute_access(self):
        s = _make_settings(listen_host="0.0.0.0", listen_port=9999, api_key="key", log_level="DEBUG")
        proxy = _SettingsProxy(s)
        assert proxy.listen_host == "0.0.0.0"
        assert proxy.listen_port == 9999
        assert proxy.api_key == "key"
        assert proxy.log_level == "DEBUG"

    def test_proxy_swap_updates_attributes(self):
        s1 = _make_settings(api_key="key1", listen_host="0.0.0.0")
        proxy = _SettingsProxy(s1)
        assert proxy.api_key == "key1"

        s2 = _make_settings(api_key="key2", listen_host="127.0.0.1", upstream_provider="requesty")
        proxy._swap(s2)
        assert proxy.api_key == "key2"
        assert proxy.listen_host == "127.0.0.1"
        assert proxy.upstream_provider == "requesty"

    def test_proxy_setattr_delegates_to_target(self):
        s = _make_settings(api_key="original")
        proxy = _SettingsProxy(s)
        proxy.api_key = "new_key"
        assert proxy.api_key == "new_key"
        assert s.api_key == "new_key"


# ---------------------------------------------------------------------------
# Stale env key cleanup tests
# ---------------------------------------------------------------------------


class TestStaleEnvCleanup:
    def test_provider_switch_clears_stale_keys(self, tmp_path):
        """When switching providers, stale provider-specific keys are removed."""
        config_dir = tmp_path / "mcptap"
        config_dir.mkdir()

        proxy_env = config_dir / "proxy.env"
        proxy_env.write_text(
            'MCP_TAP_UPSTREAM_PROVIDER="openrouter"\nMCP_TAP_LISTEN_HOST=127.0.0.1\nMCP_TAP_LISTEN_PORT=8787\n'
        )

        openrouter_env = config_dir / "openrouter.env"
        openrouter_env.write_text(
            "MCP_TAP_API_KEY=sk-or-key\nMCP_TAP_MODEL=openai/gpt-4o\nMCP_TAP_PLAN_MODE_MODEL=openai/gpt-4o\n"
        )

        requesty_env = config_dir / "requesty.env"
        requesty_env.write_text(
            "MCP_TAP_API_KEY=rqsty-key\nMCP_TAP_MODEL=nvidia/model\nMCP_TAP_PLAN_MODE_MODEL=zai/model\n"
        )

        with patch("mcptap.settings.CONFIG_DIR", config_dir):
            # Load with openrouter
            _load_env_files()
            assert os.environ.get("MCP_TAP_API_KEY") == "sk-or-key"
            assert os.environ.get("MCP_TAP_MODEL") == "openai/gpt-4o"

            # Switch to requesty
            proxy_env.write_text(
                'MCP_TAP_UPSTREAM_PROVIDER="requesty"\nMCP_TAP_LISTEN_HOST=127.0.0.1\nMCP_TAP_LISTEN_PORT=8787\n'
            )
            _load_env_files()
            assert os.environ.get("MCP_TAP_API_KEY") == "rqsty-key"
            assert os.environ.get("MCP_TAP_MODEL") == "nvidia/model"


# ---------------------------------------------------------------------------
# ConfigReloader mtime detection tests
# ---------------------------------------------------------------------------


class TestConfigReloaderMtime:
    def test_detect_changes_no_change(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            reloader._init_mtimes()
            changed = reloader._detect_changes()
            assert changed == set()

    def test_detect_changes_finds_modified_file(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            reloader._init_mtimes()

            time.sleep(0.05)
            os.utime(tmp_path / "proxy.env", None)

            changed = reloader._detect_changes()
            assert "proxy.env" in changed

    def test_detect_changes_multiple_files(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            reloader._init_mtimes()

            time.sleep(0.05)
            os.utime(tmp_path / "per-model.yaml", None)
            os.utime(tmp_path / "use_tool_hook.py", None)

            changed = reloader._detect_changes()
            assert "per-model.yaml" in changed
            assert "use_tool_hook.py" in changed
            assert "proxy.env" not in changed

    def test_detect_changes_missing_file_ignored(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            (tmp_path / "proxy.env").write_text("")
            # Don't create other files

            reloader = ConfigReloader()
            reloader._init_mtimes()
            changed = reloader._detect_changes()
            assert changed == set()


# ---------------------------------------------------------------------------
# ConfigReloader reload cascade tests
# ---------------------------------------------------------------------------


class TestConfigReloaderCascade:
    @pytest.mark.asyncio
    async def test_env_change_triggers_env_reload(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            env_cb = AsyncMock()
            intercept_cb = AsyncMock()
            per_model_cb = AsyncMock()
            tool_hook_cb = AsyncMock()

            reloader.attach(
                app=MagicMock(),
                on_env_reload=env_cb,
                on_intercept_reload=intercept_cb,
                on_per_model_reload=per_model_cb,
                on_tool_hook_reload=tool_hook_cb,
            )
            reloader._init_mtimes()

            time.sleep(0.05)
            os.utime(tmp_path / "proxy.env", None)

            changed = reloader._detect_changes()
            await reloader._handle_changes(changed)

            env_cb.assert_awaited_once()
            # env reload callback handles the full cascade, so direct callbacks
            # are NOT triggered separately.
            intercept_cb.assert_not_awaited()
            per_model_cb.assert_not_awaited()
            tool_hook_cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_intercept_yaml_change_triggers_intercept_reload(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            env_cb = AsyncMock()
            intercept_cb = AsyncMock()
            per_model_cb = AsyncMock()
            tool_hook_cb = AsyncMock()

            reloader.attach(
                app=MagicMock(),
                on_env_reload=env_cb,
                on_intercept_reload=intercept_cb,
                on_per_model_reload=per_model_cb,
                on_tool_hook_reload=tool_hook_cb,
            )
            reloader._init_mtimes()

            time.sleep(0.05)
            os.utime(tmp_path / "mcp-intercept.yaml", None)

            changed = reloader._detect_changes()
            await reloader._handle_changes(changed)

            intercept_cb.assert_awaited_once()
            env_cb.assert_not_awaited()
            per_model_cb.assert_not_awaited()
            tool_hook_cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_per_model_yaml_change_triggers_per_model_reload(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            env_cb = AsyncMock()
            intercept_cb = AsyncMock()
            per_model_cb = AsyncMock()
            tool_hook_cb = AsyncMock()

            reloader.attach(
                app=MagicMock(),
                on_env_reload=env_cb,
                on_intercept_reload=intercept_cb,
                on_per_model_reload=per_model_cb,
                on_tool_hook_reload=tool_hook_cb,
            )
            reloader._init_mtimes()

            time.sleep(0.05)
            os.utime(tmp_path / "per-model.yaml", None)

            changed = reloader._detect_changes()
            await reloader._handle_changes(changed)

            per_model_cb.assert_awaited_once()
            env_cb.assert_not_awaited()
            intercept_cb.assert_not_awaited()
            tool_hook_cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_use_tool_hook_change_triggers_tool_hook_reload(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            env_cb = AsyncMock()
            intercept_cb = AsyncMock()
            per_model_cb = AsyncMock()
            tool_hook_cb = AsyncMock()

            reloader.attach(
                app=MagicMock(),
                on_env_reload=env_cb,
                on_intercept_reload=intercept_cb,
                on_per_model_reload=per_model_cb,
                on_tool_hook_reload=tool_hook_cb,
            )
            reloader._init_mtimes()

            time.sleep(0.05)
            os.utime(tmp_path / "use_tool_hook.py", None)

            changed = reloader._detect_changes()
            await reloader._handle_changes(changed)

            tool_hook_cb.assert_awaited_once()
            env_cb.assert_not_awaited()
            intercept_cb.assert_not_awaited()
            per_model_cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_crash(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            intercept_cb = AsyncMock(side_effect=RuntimeError("boom"))

            reloader.attach(
                app=MagicMock(),
                on_env_reload=AsyncMock(),
                on_intercept_reload=intercept_cb,
                on_per_model_reload=AsyncMock(),
                on_tool_hook_reload=AsyncMock(),
            )
            reloader._init_mtimes()

            time.sleep(0.05)
            os.utime(tmp_path / "mcp-intercept.yaml", None)

            changed = reloader._detect_changes()
            await reloader._handle_changes(changed)
            intercept_cb.assert_awaited_once()


# ---------------------------------------------------------------------------
# Application-level reload callback tests
# ---------------------------------------------------------------------------


class TestReloadCallbacks:
    @pytest.mark.asyncio
    async def test_reload_per_model_config_updates_app(self):
        app = MagicMock()
        app.__getitem__ = MagicMock(side_effect=lambda k: {"old": True} if k == "per_model_config" else None)

        with patch("mcptap.config_reloader.load_per_model_config", return_value={"new": True}):
            await reload_per_model_config(app)

        app.__setitem__.assert_called_with("per_model_config", {"new": True})

    @pytest.mark.asyncio
    async def test_reload_per_model_config_error_keeps_old(self):
        app = MagicMock()
        app.__getitem__ = MagicMock(side_effect=lambda k: {"old": True} if k == "per_model_config" else None)

        with patch("mcptap.config_reloader.load_per_model_config", side_effect=RuntimeError("parse error")):
            await reload_per_model_config(app)

        app.__setitem__.assert_not_called()

    @pytest.mark.asyncio
    async def test_reload_tool_hook_updates_enabled_flag(self):
        app = MagicMock()
        hook_gateway = MagicMock()
        hook_gateway.enabled = False
        app.get = MagicMock(return_value=hook_gateway)

        # Patch on the underlying _target, not on the proxy itself
        with patch.object(global_settings._target, "use_tool_hook", "/path/to/hook.py"):
            await reload_tool_hook(app)

        assert hook_gateway.enabled is True

    @pytest.mark.asyncio
    async def test_reload_tool_hook_no_gateway_is_noop(self):
        app = MagicMock()
        app.get = MagicMock(return_value=None)

        await reload_tool_hook(app)

    @pytest.mark.asyncio
    async def test_reload_intercept_stops_old_starts_new(self):
        app = MagicMock()
        old_intercept = MagicMock()
        old_intercept.stop = AsyncMock()
        old_intercept.enabled = False
        app.get = MagicMock(return_value=old_intercept)

        with patch("mcptap.config_reloader.load_intercept_config", return_value=None):
            await reload_intercept(app)

        old_intercept.stop.assert_awaited_once()
        app.__setitem__.assert_called_once()

    @pytest.mark.asyncio
    async def test_reload_intercept_config_error_disables_intercept(self):
        app = MagicMock()
        old_intercept = MagicMock()
        old_intercept.stop = AsyncMock()
        app.get = MagicMock(return_value=old_intercept)

        with patch("mcptap.config_reloader.load_intercept_config", side_effect=RuntimeError("bad yaml")):
            await reload_intercept(app)

        old_intercept.stop.assert_awaited_once()
        new_intercept = app.__setitem__.call_args[0][1]
        assert new_intercept.enabled is False

    @pytest.mark.asyncio
    async def test_reload_env_and_propagate_calls_all_callbacks(self):
        app = MagicMock()
        old_intercept = MagicMock()
        old_intercept.stop = AsyncMock()
        old_intercept.enabled = False
        app.get = MagicMock(return_value=old_intercept)
        app.__getitem__ = MagicMock(side_effect=lambda k: {} if k == "per_model_config" else None)

        with patch("mcptap.config_reloader.reload_settings") as mock_reload:
            mock_reload.return_value = _make_settings()
            with patch("mcptap.config_reloader.load_per_model_config", return_value={"new": True}):
                with patch("mcptap.config_reloader.load_intercept_config", return_value=None):
                    await reload_env_and_propagate(app)

        mock_reload.assert_called_once()
        app.__setitem__.assert_any_call("per_model_config", {"new": True})

    @pytest.mark.asyncio
    async def test_reload_env_and_propagate_settings_error_skips_callbacks(self):
        app = MagicMock()

        with patch("mcptap.config_reloader.reload_settings", side_effect=RuntimeError("bad env")):
            await reload_env_and_propagate(app)

        app.__setitem__.assert_not_called()


# ---------------------------------------------------------------------------
# ConfigReloader lifecycle tests
# ---------------------------------------------------------------------------


class TestConfigReloaderLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            reloader._init_mtimes()
            reloader.start()
            assert reloader._task is not None
            assert not reloader._task.done()

            await asyncio.sleep(0.1)
            await reloader.stop()
            assert reloader._task is None

    @pytest.mark.asyncio
    async def test_start_twice_is_noop(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            reloader._init_mtimes()
            reloader.start()
            task1 = reloader._task
            reloader.start()
            assert reloader._task is task1
            await reloader.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self):
        reloader = ConfigReloader()
        await reloader.stop()
        assert reloader._task is None

    @pytest.mark.asyncio
    async def test_poll_loop_detects_and_handles_change(self, tmp_path):
        with patch("mcptap.config_reloader.CONFIG_DIR", tmp_path):
            for fname in [
                "proxy.env",
                "openrouter.env",
                "requesty.env",
                "mcp-intercept.yaml",
                "per-model.yaml",
                "use_tool_hook.py",
            ]:
                (tmp_path / fname).write_text("")

            reloader = ConfigReloader()
            per_model_cb = AsyncMock()

            reloader.attach(
                app=MagicMock(),
                on_env_reload=AsyncMock(),
                on_intercept_reload=AsyncMock(),
                on_per_model_reload=per_model_cb,
                on_tool_hook_reload=AsyncMock(),
            )
            reloader._init_mtimes()
            reloader.start()

            time.sleep(0.1)
            os.utime(tmp_path / "per-model.yaml", None)

            await asyncio.sleep(0.5)

            await reloader.stop()
            per_model_cb.assert_awaited_once()
