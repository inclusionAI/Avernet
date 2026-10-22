"""Typed mapping for the fastdisk publish/restore user-config block."""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.artifact_config import (
    FastdiskPublishConfig, parse_fastdisk_publish_config,
)


@pytest.mark.parametrize("block", [None, {}])
def test_missing_block_uses_defaults(block):
    config = parse_fastdisk_publish_config(block)

    assert config == FastdiskPublishConfig()


def test_block_is_validated_and_mapped():
    config = parse_fastdisk_publish_config({
        "mist_secret_name": "test-credential",
        "detect_timeout_seconds": 30,
        "upload_timeout_seconds": 900,
        "request_timeout_seconds": 10,
    })

    assert config == FastdiskPublishConfig(
        mist_secret_name="test-credential",
        detect_timeout_seconds=30,
        upload_timeout_seconds=900,
        request_timeout_seconds=10,
    )


def test_unknown_key_fails():
    with pytest.raises(ValueError, match="unknown fastdisk_publish configuration key"):
        parse_fastdisk_publish_config({"unknown": True})


def test_composition_root_wires_strategy_with_deployment_config(monkeypatch):
    from inspect import signature
    from types import SimpleNamespace
    from unittest.mock import Mock

    from agentclaw.community.di.modules import service_bot_module

    monkeypatch.setattr(service_bot_module, "read_user_config", lambda: {
        "fastdisk_publish": {"upload_timeout_seconds": 900},
    })
    module = service_bot_module.ServiceBotModule()
    args = {name: Mock() for name in signature(module.bot_build_service).parameters}
    registry = module.artifact_transfer_registry(
        device_service=args["device_service"],
        device_binding_repo=args["device_binding_repo"],
        object_storage=SimpleNamespace(bucket_name="test-bucket", endpoint=""),
        mount_config=SimpleNamespace(oss_root="/test/oss"),
    )
    args["artifact_transfers"] = registry
    service = module.bot_build_service(**args)
    assert service._artifact_transfers is registry
    strategy = registry.resolve("aicoding")
    assert registry.resolve("claude_code") is strategy
    assert strategy.config.upload_timeout_seconds == 900
    assert strategy.bucket == "test-bucket"
    assert str(strategy.oss_mount_root) == "/test/oss"
    assert strategy.executor is args["device_service"]
    assert strategy._bindings is args["device_binding_repo"]



@pytest.mark.parametrize("block", ["invalid", "", [], 0, False])
def test_non_mapping_block_fails(block):
    with pytest.raises(ValueError, match="must be a mapping"):
        parse_fastdisk_publish_config(block)
