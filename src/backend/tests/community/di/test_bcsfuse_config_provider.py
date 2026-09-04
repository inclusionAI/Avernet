"""Tests for the typed BCSFuse client configuration."""
from __future__ import annotations

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


def _bcsfuse(monkeypatch, user_config: dict):
    monkeypatch.setattr(config_module, "_user_config", lambda: dict(user_config))
    return ConfigModule().bcsfuse()


def test_bcsfuse_worker_id_defaults_to_unqualified_bot_id(monkeypatch):
    config = _bcsfuse(monkeypatch, {})

    assert getattr(config, "worker_id_with_owner", None) is False


def test_bcsfuse_worker_id_owner_setting_is_loaded(monkeypatch):
    config = _bcsfuse(
        monkeypatch,
        {"bcsfuse": {"worker_id_with_owner": True}},
    )

    assert getattr(config, "worker_id_with_owner", None) is True
