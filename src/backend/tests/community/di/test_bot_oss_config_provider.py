"""Unit test for the neutral ConfigModule.bot_oss provider key handling (B8 T4).

``ObjectStorageConfig.mist_secret_name`` was renamed to the deployment-neutral
``secret_name``. The provider must read the new ``secret_name`` YAML key but
still accept the legacy corp ``access_key_secret`` key so the corp YAML keeps
working unchanged.
"""
from __future__ import annotations

import pytest

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


@pytest.fixture
def stub_user_config(monkeypatch):
    def _set(user_config: dict) -> None:
        monkeypatch.setattr(
            config_module, "_user_config", lambda: dict(user_config)
        )

    return _set


def test_bot_oss_reads_new_secret_name_key(stub_user_config):
    stub_user_config(
        {"bot_oss_config": {"bucket_name": "b", "secret_name": "new-key"}}
    )
    out = ConfigModule().bot_oss()
    assert out.bucket_name == "b"
    assert out.secret_name == "new-key"


def test_bot_oss_falls_back_to_legacy_access_key_secret(stub_user_config):
    stub_user_config(
        {"bot_oss_config": {"access_key_secret": "legacy-key"}}
    )
    assert ConfigModule().bot_oss().secret_name == "legacy-key"


def test_bot_oss_new_key_wins_over_legacy(stub_user_config):
    stub_user_config(
        {
            "bot_oss_config": {
                "secret_name": "new-key",
                "access_key_secret": "legacy-key",
            }
        }
    )
    assert ConfigModule().bot_oss().secret_name == "new-key"


def test_bot_oss_absent_block_defaults_empty(stub_user_config):
    stub_user_config({})
    out = ConfigModule().bot_oss()
    assert out.endpoint == ""
    assert out.bucket_name == ""
    assert out.secret_name == ""
