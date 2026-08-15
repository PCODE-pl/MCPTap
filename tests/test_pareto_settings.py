"""Tests for Pareto provider configuration and isolated provider credentials."""

import os
import sys
from unittest.mock import patch

import pytest  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcptap.settings import (  # noqa: E402
    _build_settings,
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


def test_get_provider_api_key_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported provider"):
        get_provider_api_key("unknown")
