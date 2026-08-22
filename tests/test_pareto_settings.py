"""Tests for Pareto provider configuration and isolated provider credentials."""

import os
import sys
from unittest.mock import patch

import pytest  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcptap.settings import (  # noqa: E402
    _build_settings,
    _load_env_files,
    get_provider_api_key,
)


def test_build_settings_reads_global_pareto_provider(monkeypatch):
    monkeypatch.setenv("MCP_TAP_PARETO_PROVIDER", "openrouter")

    assert _build_settings().pareto_provider == "openrouter"


def test_get_provider_api_key_reads_requested_provider_env_file(tmp_path, monkeypatch):
    config_dir = tmp_path / "mcptap"
    config_dir.mkdir()
    (config_dir / "openrouter.env").write_text("MCP_TAP_API_KEY=openrouter-token\n")
    (config_dir / "requesty.env").write_text("MCP_TAP_API_KEY=requesty-token\n")
    monkeypatch.setenv("MCP_TAP_API_KEY", "active-upstream-token")

    with patch("mcptap.settings.CONFIG_DIR", config_dir):
        assert get_provider_api_key("openrouter") == "openrouter-token"
        assert get_provider_api_key("requesty") == "requesty-token"


def test_nano_gpt_provider_loads_settings_and_credentials(tmp_path, monkeypatch):
    config_dir = tmp_path / "mcptap"
    config_dir.mkdir()
    (config_dir / "proxy.env").write_text(
        "MCP_TAP_UPSTREAM_PROVIDER= NANO-GPT \nMCP_TAP_LISTEN_HOST=127.0.0.1\nMCP_TAP_LISTEN_PORT=8787\n"
    )
    (config_dir / "nano-gpt.env").write_text(
        "MCP_TAP_API_KEY=sk-nano-test\nMCP_TAP_MODEL=openai/gpt-5.6-sol\nMCP_TAP_PLAN_MODE_MODEL=openai/gpt-5.6-sol\n"
    )
    for key in (
        "MCP_TAP_API_KEY",
        "MCP_TAP_MODEL",
        "MCP_TAP_PLAN_MODE_MODEL",
        "MCP_TAP_UPSTREAM_PROVIDER",
        "MCP_TAP_USE_CHAT_COMPLETIONS",
    ):
        monkeypatch.delenv(key, raising=False)

    with patch("mcptap.settings.CONFIG_DIR", config_dir):
        _load_env_files()
        settings = _build_settings()
        assert settings.upstream_provider == "nano-gpt"
        assert settings.upstream_base_url == "https://api.nano-gpt.com/api/v1"
        assert settings.provider_env_file == "nano-gpt.env"
        assert settings.api_key == "sk-nano-test"
        assert get_provider_api_key("NANO-GPT") == "sk-nano-test"


def test_llmtr_provider_loads_settings_and_credentials(tmp_path, monkeypatch):
    config_dir = tmp_path / "mcptap"
    config_dir.mkdir()
    (config_dir / "proxy.env").write_text(
        "MCP_TAP_UPSTREAM_PROVIDER= LLMTR \nMCP_TAP_LISTEN_HOST=127.0.0.1\nMCP_TAP_LISTEN_PORT=8787\n"
    )
    (config_dir / "llmtr.env").write_text(
        "MCP_TAP_API_KEY=llmtr-test\nMCP_TAP_MODEL=zai/glm-5.2\nMCP_TAP_PLAN_MODE_MODEL=zai/glm-5.2\n"
        "MCP_TAP_USE_CHAT_COMPLETIONS=true\n"
    )
    for key in (
        "MCP_TAP_API_KEY",
        "MCP_TAP_MODEL",
        "MCP_TAP_PLAN_MODE_MODEL",
        "MCP_TAP_UPSTREAM_PROVIDER",
        "MCP_TAP_USE_CHAT_COMPLETIONS",
    ):
        monkeypatch.delenv(key, raising=False)

    with patch("mcptap.settings.CONFIG_DIR", config_dir):
        _load_env_files()
        settings = _build_settings()
        assert settings.upstream_provider == "llmtr"
        assert settings.upstream_base_url == "https://llmtr.com/v1"
        assert settings.provider_env_file == "llmtr.env"
        assert settings.api_key == "llmtr-test"
        assert settings.use_chat_completions is True
        assert get_provider_api_key("LLMTR") == "llmtr-test"


def test_get_provider_api_key_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported provider"):
        get_provider_api_key("unknown")


def test_chat_completions_mode_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("MCP_TAP_USE_CHAT_COMPLETIONS", raising=False)

    assert _build_settings().use_chat_completions is False
