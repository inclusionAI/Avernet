"""Typed mapping for the fastdisk publish/restore user-config block."""
from __future__ import annotations

import pytest

from agentclaw.community.core.service_bot.services.fastdisk_publish import (
    FastdiskPublishConfig,
)
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.fastdisk_publish_config_module import (
    FastdiskPublishConfigModule,
)


@pytest.mark.parametrize("block", [None, {}, "invalid"])
def test_missing_or_invalid_block_uses_defaults(monkeypatch, block):
    monkeypatch.setattr(config_module, "read_user_config", lambda: {"fastdisk_publish": block})

    config = FastdiskPublishConfigModule().fastdisk_publish()

    assert config == FastdiskPublishConfig()


def test_block_is_validated_and_mapped(monkeypatch):
    monkeypatch.setattr(
        config_module,
        "read_user_config",
        lambda: {
            "fastdisk_publish": {
                "mist_secret_name": "test-credential",
                "detect_timeout_seconds": 30,
                "upload_timeout_seconds": 900,
                "request_timeout_seconds": 10,
            }
        },
    )

    config = FastdiskPublishConfigModule().fastdisk_publish()

    assert config == FastdiskPublishConfig(
        mist_secret_name="test-credential",
        detect_timeout_seconds=30,
        upload_timeout_seconds=900,
        request_timeout_seconds=10,
    )


def test_unknown_key_fails(monkeypatch):
    monkeypatch.setattr(
        config_module,
        "read_user_config",
        lambda: {"fastdisk_publish": {"unknown": True}},
    )

    with pytest.raises(ValueError, match="unknown fastdisk_publish configuration key"):
        FastdiskPublishConfigModule().fastdisk_publish()
