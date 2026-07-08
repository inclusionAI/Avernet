"""Unit tests for PublishConfig, PublishBatchConfig, StageConfig."""

from secbaas.api.device_manage import DeployConfig
from secbaas.api.publish_manage import (
    PublishBatchConfig,
    PublishConfig,
    PublishType,
    RestartScope,
    StageConfig,
)


class TestPublishConfig:
    def test_round_trip_with_caller_keys(self) -> None:
        config = PublishConfig(
            bot_name="test",
            replica_desired=5,
            batch_capacity=3,
            cooldown_seconds=0,
            reason="deploy",
            restart_scope=RestartScope.ALL,
            restart_reason="user_initiated",
        )
        d = config.model_dump(exclude_none=True)
        config2 = PublishConfig.model_validate(d)
        assert config2.bot_name == "test"
        assert config2.replica_desired == 5
        assert (
            config2.model_dump().get("entity_id") is None
        )  # entity_id not a typed field

    def test_with_stages(self) -> None:
        config = PublishConfig.model_validate(
            {
                "stages": {
                    "PREPUB": {"device_count": 2, "batch_capacity": 3},
                    "GRAY": {"device_count": 4},
                },
                "drain_timeout_seconds": 60,
            }
        )
        assert config.stages["PREPUB"].device_count == 2
        assert config.stages["PREPUB"].batch_capacity == 3
        assert config.drain_timeout_seconds == 60

    def test_with_deploy_config(self) -> None:
        config = PublishConfig(
            deploy_config=DeployConfig(
                after_create_cmd_hook="/scripts/after_create.sh",
            ),
        )
        assert config.deploy_config is not None
        d = config.model_dump(exclude_none=True)
        config2 = PublishConfig.model_validate(d)
        assert config2.deploy_config is not None
        assert config2.deploy_config.after_create_cmd_hook == "/scripts/after_create.sh"

    def test_defaults_for_type_create(self) -> None:
        defaults = PublishConfig.get_defaults_for_type(PublishType.CREATE)
        config = PublishConfig.model_validate(defaults)
        assert "PREPUB" in config.stages
        assert config.stages["PREPUB"].device_count == 2
        assert config.stages["PREPUB"].pause_for_approval is True

    def test_extra_allow(self) -> None:
        config = PublishConfig.model_validate({"custom_key": "val"})
        dumped = config.model_dump()
        assert "custom_key" in dumped


class TestPublishBatchConfig:
    def test_defaults(self) -> None:
        config = PublishBatchConfig.model_validate({})
        assert config.stage == "UNKNOWN"
        assert config.cooldown_seconds == 0
        assert config.device_count is None

    def test_deserialization(self) -> None:
        config = PublishBatchConfig.model_validate(
            {
                "stage": "PREPUB",
                "cooldown_seconds": 0,
                "device_count": 5,
            }
        )
        assert config.stage == "PREPUB"
        assert config.cooldown_seconds == 0
        assert config.device_count == 5

    def test_round_trip(self) -> None:
        config = PublishBatchConfig(stage="GRAY", cooldown_seconds=0, device_count=3)
        d = config.model_dump(exclude_none=True)
        config2 = PublishBatchConfig.model_validate(d)
        assert config2.stage == "GRAY"
        assert config2.cooldown_seconds == 0


class TestStageConfig:
    def test_defaults(self) -> None:
        cfg = StageConfig()
        assert cfg.device_count == 0
        assert cfg.batch_capacity == 5
        assert cfg.cooldown_seconds == 0
        assert cfg.pause_for_approval is False

    def test_from_dict(self) -> None:
        cfg = StageConfig.model_validate(
            {
                "device_count": 10,
                "batch_capacity": 3,
                "cooldown_seconds": 0,
                "pause_for_approval": True,
            }
        )
        assert cfg.device_count == 10
        assert cfg.pause_for_approval is True
