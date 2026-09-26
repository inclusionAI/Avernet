"""Unit tests for device creation configuration models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from secbaas.community.api.device_manage import (
    ArcaCreateConfig,
    DeployConfig,
    DeviceConfig,
    DeviceCreateConfig,
    DeviceCreateConfigUnion,
    DockerCreateConfig,
    DockerDeviceConfig,
    LocalCreateConfig,
    PoolabCreateConfig,
    SigmaCreateConfig,
)


class TestDeviceCreateConfig:
    """Test base DeviceCreateConfig."""

    def test_defaults(self):
        cfg = DeviceCreateConfig()
        assert cfg.name is None
        assert cfg.description is None

    def test_with_name(self):
        cfg = DeviceCreateConfig(name="my-device")
        assert cfg.name == "my-device"


class TestArcaCreateConfig:
    """Test ArcaCreateConfig model."""

    def test_defaults(self):
        cfg = ArcaCreateConfig()
        assert cfg.ttl_in_minutes == 1440
        assert cfg.template_id is None

    def test_custom_ttl(self):
        cfg = ArcaCreateConfig(ttl_in_minutes=60)
        assert cfg.ttl_in_minutes == 60

    def test_ttl_below_minimum(self):
        with pytest.raises(ValidationError):
            ArcaCreateConfig(ttl_in_minutes=5)

    def test_with_envs(self):
        cfg = ArcaCreateConfig(envs={"KEY": "val"})
        assert cfg.envs == {"KEY": "val"}

    def test_inherits_base(self):
        cfg = ArcaCreateConfig(name="arca-device", description="An Arca device")
        assert cfg.name == "arca-device"
        assert cfg.description == "An Arca device"

    def test_metadata(self):
        cfg = ArcaCreateConfig(metadata={"key": "val"})
        assert cfg.metadata == {"key": "val"}

    def test_docker_image_defaults_to_none(self):
        cfg = ArcaCreateConfig()
        assert cfg.docker_image is None

    def test_docker_image_custom_value(self):
        cfg = ArcaCreateConfig(docker_image="my-custom-image:v2")
        assert cfg.docker_image == "my-custom-image:v2"


class TestSigmaCreateConfig:
    """Test SigmaCreateConfig model."""

    def test_defaults(self):
        cfg = SigmaCreateConfig()
        assert cfg.region is None
        assert cfg.zone is None

    def test_with_fields(self):
        cfg = SigmaCreateConfig(
            region="us-east-1",
            zone="us-east-1a",
            vpc_config={"vpc_id": "vpc-abc"},
        )
        assert cfg.region == "us-east-1"
        assert cfg.zone == "us-east-1a"
        assert cfg.vpc_config == {"vpc_id": "vpc-abc"}

    def test_inherits_base(self):
        cfg = SigmaCreateConfig(name="sigma-device")
        assert cfg.name == "sigma-device"


class TestLocalCreateConfig:
    """Test LocalCreateConfig model."""

    def test_required_fields(self):
        cfg = LocalCreateConfig(
            user_id="u-1",
            machine_id="m-1",
            tc_bot_id="bot-001",
            agent_code="agent-x",
        )
        assert cfg.user_id == "u-1"
        assert cfg.machine_id == "m-1"
        assert cfg.tc_bot_id == "bot-001"
        assert cfg.agent_code == "agent-x"

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            LocalCreateConfig()

    def test_optional_envs(self):
        cfg = LocalCreateConfig(
            user_id="u-1",
            machine_id="m-1",
            tc_bot_id="bot-001",
            agent_code="agent-x",
            envs={"KEY": "val"},
        )
        assert cfg.envs == {"KEY": "val"}

    def test_inherits_base(self):
        cfg = LocalCreateConfig(
            user_id="u-1",
            machine_id="m-1",
            tc_bot_id="bot-001",
            agent_code="agent-x",
            name="local-device",
        )
        assert cfg.name == "local-device"


class TestDeviceCreateConfigUnion:
    """Test DeviceCreateConfigUnion type alias."""

    def test_arca_instance(self):
        cfg: DeviceCreateConfigUnion = ArcaCreateConfig(ttl_in_minutes=60)
        assert isinstance(cfg, ArcaCreateConfig)

    def test_sigma_instance(self):
        cfg: DeviceCreateConfigUnion = SigmaCreateConfig(region="us-east-1")
        assert isinstance(cfg, SigmaCreateConfig)

    def test_local_instance(self):
        cfg: DeviceCreateConfigUnion = LocalCreateConfig(
            user_id="u-1",
            machine_id="m-1",
            tc_bot_id="bot-001",
            agent_code="agent-x",
        )
        assert isinstance(cfg, LocalCreateConfig)


class TestDeviceConfig:
    """Test DeviceConfig model behavior."""

    def test_empty_config(self) -> None:
        """DeviceConfig can be created with no fields."""
        config = DeviceConfig()
        assert config.template_uuid is None
        assert config.deploy_config is None

    def test_with_template_uuid(self) -> None:
        """DeviceConfig accepts template_uuid string."""
        config = DeviceConfig(template_uuid="test-uuid-abc123")
        assert config.template_uuid == "test-uuid-abc123"

    def test_with_deploy_config(self) -> None:
        """DeviceConfig accepts deploy_config with DeployConfig."""
        deploy_config = DeployConfig(
            after_create_cmd_hook="/scripts/after_create.sh",
            after_create_hook_wait_seconds=600,
        )
        config = DeviceConfig(deploy_config=deploy_config)
        assert config.deploy_config is not None
        assert config.deploy_config.after_create_cmd_hook == "/scripts/after_create.sh"

    def test_with_all_fields(self) -> None:
        """DeviceConfig accepts all typed fields."""
        deploy_config = DeployConfig(
            after_create_cmd_hook="/scripts/after_create.sh",
        )
        config = DeviceConfig(
            template_uuid="test-uuid-xyz",
            deploy_config=deploy_config,
        )
        assert config.template_uuid == "test-uuid-xyz"
        assert config.deploy_config is not None
        assert config.deploy_config.after_create_cmd_hook == "/scripts/after_create.sh"


class TestDeviceConfigExtraAllow:
    """Test DeviceConfig extra="allow" behavior."""

    def test_unknown_keys_preserved(self) -> None:
        """Unknown keys are preserved in model."""
        config = DeviceConfig.model_validate(
            {"custom_key": "custom_value", "another": 123}
        )
        dumped = config.model_dump()
        assert dumped["custom_key"] == "custom_value"
        assert dumped["another"] == 123

    def test_model_dump_includes_extra_fields(self) -> None:
        """model_dump() includes extra fields."""
        config = DeviceConfig.model_validate(
            {"deploy_config": None, "custom_key": "custom_value"}
        )
        data = config.model_dump()
        assert "custom_key" in data
        assert data["custom_key"] == "custom_value"

    def test_model_validate_with_extra_fields(self) -> None:
        """model_validate() correctly parses extra fields."""
        config = DeviceConfig.model_validate(
            {
                "template_uuid": "abc",
                "extra_field": "extra_value",
            }
        )
        assert config.template_uuid == "abc"
        dumped = config.model_dump()
        assert dumped["extra_field"] == "extra_value"

    def test_round_trip_with_extra_fields(self) -> None:
        """Round trip: validate -> dump -> validate preserves extra fields."""
        original = {
            "template_uuid": "uuid123",
            "custom_key": "custom_value",
        }
        config1 = DeviceConfig.model_validate(original)
        dumped = config1.model_dump()
        config2 = DeviceConfig.model_validate(dumped)
        assert config2.template_uuid == "uuid123"
        assert config2.model_dump()["custom_key"] == "custom_value"

    def test_legacy_nas_mappings_treated_as_extra(self) -> None:
        """Legacy DB records with nas_mappings key are tolerated as extra fields."""
        config = DeviceConfig.model_validate(
            {
                "nas_mappings": [
                    {
                        "nas_remote": "svr:/data/test",
                        "nas_local": "/home/admin/data",
                        "nas_param": "nfsvers=3",
                    }
                ],
                "bot_template_dir": "/old/path",
            }
        )
        dumped = config.model_dump()
        assert dumped["nas_mappings"] is not None
        assert dumped["bot_template_dir"] == "/old/path"


class TestDeviceConfigSerialization:
    """Test DeviceConfig serialization for database storage."""

    def test_model_dump_exclude_none(self) -> None:
        """model_dump(exclude_none=True) omits None fields."""
        config = DeviceConfig()
        data = config.model_dump(exclude_none=True)
        assert "deploy_config" not in data

    def test_model_dump_exclude_none_keeps_values(self) -> None:
        """model_dump(exclude_none=True) keeps non-None values."""
        deploy_config = DeployConfig(
            after_create_cmd_hook="/scripts/after_create.sh",
            after_create_hook_wait_seconds=600,
        )
        config = DeviceConfig(deploy_config=deploy_config)
        data = config.model_dump(exclude_none=True)
        assert "deploy_config" in data
        assert (
            data["deploy_config"]["after_create_cmd_hook"] == "/scripts/after_create.sh"
        )

    def test_empty_config_serialization(self) -> None:
        """Empty config serializes correctly for database."""
        config = DeviceConfig()
        data = config.model_dump(exclude_none=True)
        assert data == {"metadata": {}}

    def test_config_with_extra_serialization(self) -> None:
        """Config with extra fields serializes correctly."""
        config = DeviceConfig.model_validate(
            {
                "custom_field": "custom_value",
            }
        )
        data = config.model_dump(exclude_none=True)
        assert data["custom_field"] == "custom_value"


class TestPoolabCreateConfig:
    """Test PoolabCreateConfig model."""

    def test_required_fields(self):
        """PoolabCreateConfig requires poolab_user_id."""
        cfg = PoolabCreateConfig(poolab_user_id="user1")
        assert cfg.poolab_user_id == "user1"
        assert cfg.poolab_tenant_id is None
        assert cfg.poolab_image_id is None
        assert cfg.poolab_envs is None

    def test_inherits_base(self):
        """PoolabCreateConfig inherits name and description from DeviceCreateConfig."""
        cfg = PoolabCreateConfig(
            poolab_user_id="user1",
            name="poolab-device",
            description="test device",
        )
        assert cfg.name == "poolab-device"
        assert cfg.description == "test device"

    def test_with_optional_fields(self):
        """PoolabCreateConfig accepts all optional fields."""
        cfg = PoolabCreateConfig(
            poolab_user_id="user1",
            poolab_tenant_id="100001",
            poolab_image_id="img-abc",
            poolab_envs={"KEY": "val"},
        )
        assert cfg.poolab_tenant_id == "100001"
        assert cfg.poolab_image_id == "img-abc"
        assert cfg.poolab_envs == {"KEY": "val"}


# ==================== DockerCreateConfig ====================


class TestDockerCreateConfig:
    """Tests for DockerCreateConfig model."""

    def test_all_fields_optional(self):
        """WHEN no fields provided, THEN DockerCreateConfig constructs successfully."""
        cfg = DockerCreateConfig()
        assert cfg.image is None
        assert cfg.container_port is None
        assert cfg.envs is None
        assert cfg.cpu_limit is None
        assert cfg.memory_limit is None
        assert cfg.name is None
        assert cfg.description is None

    def test_with_all_fields(self):
        """WHEN all fields provided, THEN stored correctly."""
        cfg = DockerCreateConfig(
            image="nginx",
            container_port=80,
            envs={"NODE_ENV": "prod"},
            cpu_limit=2.0,
            memory_limit="1g",
            name="dev",
            description="desc",
        )
        assert cfg.image == "nginx"
        assert cfg.container_port == 80
        assert cfg.envs == {"NODE_ENV": "prod"}
        assert cfg.cpu_limit == 2.0
        assert cfg.memory_limit == "1g"
        assert cfg.name == "dev"
        assert cfg.description == "desc"

    def test_container_port_range_too_low(self):
        """WHEN container_port=0, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerCreateConfig(container_port=0)

    def test_container_port_range_too_high(self):
        """WHEN container_port=65536, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerCreateConfig(container_port=65536)

    def test_cpu_limit_range_too_low(self):
        """WHEN cpu_limit=0.05, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerCreateConfig(cpu_limit=0.05)

    def test_cpu_limit_range_too_high(self):
        """WHEN cpu_limit=65, THEN ValidationError."""
        with pytest.raises(ValidationError):
            DockerCreateConfig(cpu_limit=65)


# ==================== DockerDeviceConfig ====================


class TestDockerDeviceConfig:
    """Tests for DockerDeviceConfig model and to_create_config()."""

    def test_to_create_config_passes_fields(self):
        """WHEN to_create_config called, THEN fields pass to DockerCreateConfig."""
        detail = DockerDeviceConfig(
            image="alpine:latest",
            container_port=8080,
            memory_limit="512m",
            envs={"KEY": "val"},
            cpu_limit=2.0,
            name="test",
            description="desc",
        )
        result = detail.to_create_config()
        assert isinstance(result, DockerCreateConfig)
        assert result.image == "alpine:latest"
        assert result.container_port == 8080
        assert result.memory_limit == "512m"
        assert result.envs == {"KEY": "val"}
        assert result.cpu_limit == 2.0
        assert result.name == "test"
        assert result.description == "desc"
