"""Verify DormantConfig is built from env / YAML correctly.

DormantConfig only carries non-secret knobs (dry_run). The Bearer token
is resolved separately via SecretResolver — see
``test_token_resolver.py`` and ``test_internal_auth.py``.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from agentclaw.community.di import config as cfg


@pytest.mark.unit
def test_dormant_config_defaults():
    c = cfg.DormantConfig()
    assert c.dry_run is True   # default safe — never accidentally recycle


@pytest.mark.unit
def test_dormant_config_dry_run_from_yaml(monkeypatch):
    """dry_run reads YAML when env not set."""
    monkeypatch.delenv("DORMANT_DRY_RUN", raising=False)
    from agentclaw.community.di.modules.config_module import ConfigModule
    with patch(
        "agentclaw.community.di.modules.config_module._block",
        return_value={"dry_run": False},
    ):
        c = ConfigModule().dormant_config()
    assert c.dry_run is False


@pytest.mark.unit
def test_dormant_config_dry_run_env_overrides_yaml(monkeypatch):
    """env DORMANT_DRY_RUN wins over YAML."""
    monkeypatch.setenv("DORMANT_DRY_RUN", "false")
    from agentclaw.community.di.modules.config_module import ConfigModule
    with patch(
        "agentclaw.community.di.modules.config_module._block",
        return_value={"dry_run": True},  # YAML says true but env wins
    ):
        c = ConfigModule().dormant_config()
    assert c.dry_run is False


@pytest.mark.unit
def test_dormant_config_dry_run_missing_defaults_true(monkeypatch):
    """No env, no YAML → default True (safe)."""
    monkeypatch.delenv("DORMANT_DRY_RUN", raising=False)
    from agentclaw.community.di.modules.config_module import ConfigModule
    with patch(
        "agentclaw.community.di.modules.config_module._block",
        return_value={},
    ):
        c = ConfigModule().dormant_config()
    assert c.dry_run is True
