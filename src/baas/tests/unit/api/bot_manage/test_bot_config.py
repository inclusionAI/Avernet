"""Unit tests for BotConfig and DeployConfig."""

import pytest
from pydantic import ValidationError

from secbaas.community.api.bot_manage import BotConfig
from secbaas.community.api.device_manage import DeployConfig

# ==================== DeployConfig ====================


class TestDeployConfig:
    def test_valid(self) -> None:
        cfg = DeployConfig()
        assert cfg.after_create_cmd_hook is None
        assert cfg.after_create_hook_wait_seconds == 300
        assert cfg.before_destroy_cmd_hook is None
        assert cfg.before_destroy_hook_wait_seconds == 300

    def test_with_lifecycle_hooks(self) -> None:
        cfg = DeployConfig(
            after_create_cmd_hook="/scripts/after_create.sh",
            after_create_hook_wait_seconds=600,
            before_destroy_cmd_hook="/scripts/before_destroy.sh",
            before_destroy_hook_wait_seconds=120,
        )
        assert cfg.after_create_cmd_hook == "/scripts/after_create.sh"
        assert cfg.after_create_hook_wait_seconds == 600
        assert cfg.before_destroy_cmd_hook == "/scripts/before_destroy.sh"
        assert cfg.before_destroy_hook_wait_seconds == 120

    def test_invalid_wait_seconds(self) -> None:
        with pytest.raises(ValidationError):
            DeployConfig(after_create_hook_wait_seconds=-1)


# ==================== BotConfig ====================


class TestBotConfig:
    def test_legacy_dict(self) -> None:
        legacy = {
            "template_id": 5,
            "owner_id": "u1",
            "share_policy": {"public": True},
            "unknown_key": "val",
        }
        config = BotConfig.model_validate(legacy)
        assert config.share_policy == {"public": True}
        assert config.deploy_config is None

    def test_with_deploy_config(self) -> None:
        config = BotConfig(
            deploy_config=DeployConfig(
                after_create_cmd_hook="/scripts/after_create.sh",
            )
        )
        assert config.deploy_config is not None
        assert config.deploy_config.after_create_cmd_hook == "/scripts/after_create.sh"

    def test_unknown_keys_preserved(self) -> None:
        config = BotConfig.model_validate({"unknown_key": "val"})
        dumped = config.model_dump()
        assert "unknown_key" in dumped

    def test_round_trip(self) -> None:
        config = BotConfig(
            share_policy={"x": 1},
            deploy_config=DeployConfig(
                after_create_cmd_hook="/scripts/test.sh",
                after_create_hook_wait_seconds=600,
            ),
        )
        d = config.model_dump(exclude_none=True)
        config2 = BotConfig.model_validate(d)
        assert config2.share_policy == config.share_policy
        assert config2.deploy_config is not None
        assert config2.deploy_config.after_create_cmd_hook == "/scripts/test.sh"

    def test_empty_extra_config(self) -> None:
        config = BotConfig.model_validate({})
        assert config.share_policy is None
        assert config.sla_grade == "standard"
        assert config.deploy_config is None
