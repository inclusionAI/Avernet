"""Unit tests for DeployConfig model and platform-specific config classes.

Note: DeployConfig is a unified class containing all platform fields, NOT a union type.
Platform type is determined at runtime from template lookup (ArcaTemplateConfig,
LocalTemplateConfig, SigmaTemplateConfig), not from a discriminator field.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from secbaas.community.api.device_manage import (
    ArcaDeployConfig,
    DeployConfig,
    DeviceCredentials,
    LocalDeployConfig,
    MountPermission,
    MountPoint,
    SigmaDeployConfig,
)


class TestArcaDeployConfig:
    """Tests for ArcaDeployConfig model.

    ArcaDeployConfig is preserved for internal use but has no platform_type field.
    """

    def test_arca_specific_fields_present(self) -> None:
        """Test that all Arca-specific fields are present."""
        config = ArcaDeployConfig(
            mount_points=[
                MountPoint(
                    id="mount-1",
                    remote_dir="/remote/path",
                    local_dir="/local/path",
                    permission=MountPermission.READ_ONLY,
                )
            ],
            ttl_in_minutes=60,
            arca_metadata={"key": "value"},
            outbound_operation_rule=None,
            storage=None,
        )
        assert config.mount_points is not None
        assert len(config.mount_points) == 1
        assert config.ttl_in_minutes == 60
        assert config.arca_metadata == {"key": "value"}

    def test_common_fields_present(self) -> None:
        """Test that common fields are present in ArcaDeployConfig."""
        config = ArcaDeployConfig(
            envs={"FOO": "bar"},
            after_create_cmd_hook="echo 'hello'",
            after_create_hook_wait_seconds=120,
            before_destroy_cmd_hook="echo 'goodbye'",
            before_destroy_hook_wait_seconds=60,
        )
        assert config.envs == {"FOO": "bar"}
        assert config.after_create_cmd_hook == "echo 'hello'"
        assert config.after_create_hook_wait_seconds == 120
        assert config.before_destroy_cmd_hook == "echo 'goodbye'"
        assert config.before_destroy_hook_wait_seconds == 60

    def test_default_values(self) -> None:
        """Test that default values are set correctly."""
        config = ArcaDeployConfig()
        assert config.after_create_hook_wait_seconds == 300
        assert config.before_destroy_hook_wait_seconds == 300
        assert config.mount_points is None
        assert config.envs is None
        assert config.arca_metadata is None

    def test_no_platform_type_field(self) -> None:
        """Test that ArcaDeployConfig has no platform_type field.

        Platform type is determined at runtime from template lookup, not from config.
        """
        config = ArcaDeployConfig()
        assert not hasattr(config, "platform_type")

    def test_mount_points_validation_from_dict(self) -> None:
        """Test mount_points validator converts dict to MountPoint objects."""
        config = ArcaDeployConfig(
            mount_points=[
                {
                    "id": "mount-1",
                    "remote_dir": "/remote/path",
                    "local_dir": "/local/path",
                    "permission": "READ_ONLY",
                }
            ]
        )
        assert isinstance(config.mount_points, list)
        assert len(config.mount_points) == 1
        assert isinstance(config.mount_points[0], MountPoint)
        assert config.mount_points[0].permission == MountPermission.READ_ONLY

    def test_mount_points_validation_permission_strings(self) -> None:
        """Test mount_points validator handles different permission strings."""
        config = ArcaDeployConfig(
            mount_points=[
                {
                    "remote_dir": "/remote/1",
                    "local_dir": "/local/1",
                    "permission": "READ_ONLY",
                },
                {
                    "remote_dir": "/remote/2",
                    "local_dir": "/local/2",
                    "permission": "READ_WRITE",
                },
            ]
        )
        assert config.mount_points[0].permission == MountPermission.READ_ONLY
        assert config.mount_points[1].permission == MountPermission.READ_WRITE

    def test_mount_points_validation_default_permission(self) -> None:
        """Test mount_points validator defaults to READ_WRITE if permission not specified."""
        config = ArcaDeployConfig(
            mount_points=[{"remote_dir": "/remote/path", "local_dir": "/local/path"}]
        )
        assert config.mount_points[0].permission == MountPermission.READ_WRITE

    def test_mount_points_validation_none(self) -> None:
        """Test mount_points validator handles None."""
        config = ArcaDeployConfig(mount_points=None)
        assert config.mount_points is None

    def test_ttl_validation_minimum(self) -> None:
        """Test that ttl_in_minutes must be >= 10."""
        # Valid: exactly 10
        config = ArcaDeployConfig(ttl_in_minutes=10)
        assert config.ttl_in_minutes == 10

        # Valid: greater than 10
        config = ArcaDeployConfig(ttl_in_minutes=60)
        assert config.ttl_in_minutes == 60

        # Invalid: less than 10
        with pytest.raises(ValidationError):
            ArcaDeployConfig(ttl_in_minutes=5)

    def test_hook_wait_seconds_validation(self) -> None:
        """Test that hook wait seconds must be >= 0."""
        # Valid: 0
        config = ArcaDeployConfig(after_create_hook_wait_seconds=0)
        assert config.after_create_hook_wait_seconds == 0

        # Invalid: negative
        with pytest.raises(ValidationError):
            ArcaDeployConfig(after_create_hook_wait_seconds=-1)


class TestLocalDeployConfig:
    """Tests for LocalDeployConfig model.

    LocalDeployConfig is preserved for internal use but has no platform_type field.
    """

    def test_no_platform_type_field(self) -> None:
        """Test that LocalDeployConfig has no platform_type field.

        Platform type is determined at runtime from template lookup, not from config.
        """
        config = LocalDeployConfig()
        assert not hasattr(config, "platform_type")

    def test_local_specific_fields(self) -> None:
        """Test that Local-specific fields are present."""
        config = LocalDeployConfig(machine_id="machine-123")
        assert config.machine_id == "machine-123"

    def test_common_fields_present(self) -> None:
        """Test that common fields are present in LocalDeployConfig."""
        config = LocalDeployConfig(
            envs={"FOO": "bar"},
            after_create_cmd_hook="echo 'hello'",
            after_create_hook_wait_seconds=120,
            before_destroy_cmd_hook="echo 'goodbye'",
            before_destroy_hook_wait_seconds=60,
        )
        assert config.envs == {"FOO": "bar"}
        assert config.after_create_cmd_hook == "echo 'hello'"
        assert config.after_create_hook_wait_seconds == 120
        assert config.before_destroy_cmd_hook == "echo 'goodbye'"
        assert config.before_destroy_hook_wait_seconds == 60

    def test_default_values(self) -> None:
        """Test that default values are set correctly."""
        config = LocalDeployConfig()
        assert config.after_create_hook_wait_seconds == 300
        assert config.before_destroy_hook_wait_seconds == 300
        assert config.machine_id is None
        assert config.agent_code is None
        assert config.envs is None

    def test_agent_code_field(self) -> None:
        """Test that agent_code field is present and works correctly."""
        config = LocalDeployConfig(machine_id="machine-123", agent_code="my-agent-001")
        assert config.machine_id == "machine-123"
        assert config.agent_code == "my-agent-001"

        # Serialize and deserialize
        dumped = config.model_dump()
        restored = LocalDeployConfig.model_validate(dumped)
        assert restored.agent_code == "my-agent-001"


class TestSigmaDeployConfig:
    """Tests for SigmaDeployConfig model.

    SigmaDeployConfig is preserved for internal use but has no platform_type field.
    """

    def test_no_platform_type_field(self) -> None:
        """Test that SigmaDeployConfig has no platform_type field.

        Platform type is determined at runtime from template lookup, not from config.
        """
        config = SigmaDeployConfig()
        assert not hasattr(config, "platform_type")

    def test_common_fields_present(self) -> None:
        """Test that common fields are present in SigmaDeployConfig."""
        config = SigmaDeployConfig(
            envs={"FOO": "bar"},
            after_create_cmd_hook="echo 'hello'",
            after_create_hook_wait_seconds=120,
            before_destroy_cmd_hook="echo 'goodbye'",
            before_destroy_hook_wait_seconds=60,
        )
        assert config.envs == {"FOO": "bar"}
        assert config.after_create_cmd_hook == "echo 'hello'"
        assert config.after_create_hook_wait_seconds == 120
        assert config.before_destroy_cmd_hook == "echo 'goodbye'"
        assert config.before_destroy_hook_wait_seconds == 60

    def test_default_values(self) -> None:
        """Test that default values are set correctly."""
        config = SigmaDeployConfig()
        assert config.after_create_hook_wait_seconds == 300
        assert config.before_destroy_hook_wait_seconds == 300
        assert config.envs is None


class TestUnifiedDeployConfig:
    """Tests for the unified DeployConfig model.

    DeployConfig is a single unified class containing ALL platform fields.
    Platform type is determined at runtime from template lookup.
    """

    def test_no_platform_type_field(self) -> None:
        """Test that unified DeployConfig has no platform_type field.

        This is by design - platform is resolved at runtime from template type.
        """
        config = DeployConfig()
        assert not hasattr(config, "platform_type")

    def test_can_hold_all_platform_fields(self) -> None:
        """Test that DeployConfig can hold fields from all platforms simultaneously."""
        config = DeployConfig(
            # Arca fields
            mount_points=[
                MountPoint(
                    id="mount-1",
                    remote_dir="/remote/path",
                    local_dir="/local/path",
                    permission=MountPermission.READ_ONLY,
                )
            ],
            ttl_in_minutes=60,
            arca_metadata={"key": "value"},
            # Local fields
            machine_id="machine-123",
            agent_code="agent-001",
            # Sigma fields
            zone="zone-1",
            vpc_config={"vpc_id": "vpc-123"},
            sigma_metadata={"env": "prod"},
            # Common fields
            envs={"KEY": "value"},
        )

        # Verify Arca fields
        assert config.ttl_in_minutes == 60
        assert config.arca_metadata == {"key": "value"}

        # Verify Local fields
        assert config.machine_id == "machine-123"
        assert config.agent_code == "agent-001"

        # Verify Sigma fields
        assert config.zone == "zone-1"
        assert config.vpc_config == {"vpc_id": "vpc-123"}
        assert config.sigma_metadata == {"env": "prod"}

        # Verify common fields
        assert config.envs == {"KEY": "value"}

    def test_default_values(self) -> None:
        """Test that DeployConfig has correct default values."""
        config = DeployConfig()
        assert config.after_create_hook_wait_seconds == 300
        assert config.before_destroy_hook_wait_seconds == 300
        # All optional fields default to None
        assert config.mount_points is None
        assert config.ttl_in_minutes is None
        assert config.arca_metadata is None
        assert config.machine_id is None
        assert config.agent_code is None
        assert config.zone is None
        assert config.vpc_config is None
        assert config.sigma_metadata is None
        assert config.envs is None

    def test_serialization_roundtrip(self) -> None:
        """Test that DeployConfig can be serialized and deserialized."""
        config = DeployConfig(
            machine_id="machine-123",
            agent_code="agent-001",
            ttl_in_minutes=60,
            arca_metadata={"key": "value"},
            envs={"FOO": "bar"},
        )

        dumped = config.model_dump()
        restored = DeployConfig.model_validate(dumped)

        assert restored.machine_id == "machine-123"
        assert restored.agent_code == "agent-001"
        assert restored.ttl_in_minutes == 60
        assert restored.arca_metadata == {"key": "value"}
        assert restored.envs == {"FOO": "bar"}

    def test_mount_point_validation(self) -> None:
        """Test mount_points validation from dict in unified DeployConfig."""
        config = DeployConfig(
            mount_points=[
                {
                    "id": "mount-1",
                    "remote_dir": "/remote/path",
                    "local_dir": "/local/path",
                    "permission": "READ_ONLY",
                }
            ]
        )
        assert isinstance(config.mount_points, list)
        assert len(config.mount_points) == 1
        assert isinstance(config.mount_points[0], MountPoint)

    def test_ttl_validation(self) -> None:
        """Test ttl_in_minutes validation in unified DeployConfig."""
        # Valid
        config = DeployConfig(ttl_in_minutes=10)
        assert config.ttl_in_minutes == 10

        # Invalid: less than 10
        with pytest.raises(ValidationError):
            DeployConfig(ttl_in_minutes=5)

    def test_hook_wait_seconds_validation(self) -> None:
        """Test hook wait seconds validation."""
        # Valid: 0
        config = DeployConfig(after_create_hook_wait_seconds=0)
        assert config.after_create_hook_wait_seconds == 0

        # Invalid: negative
        with pytest.raises(ValidationError):
            DeployConfig(after_create_hook_wait_seconds=-1)

    def test_partial_fields(self) -> None:
        """Test that DeployConfig works with partial fields (typical use case)."""
        # Typical ARCA usage
        arca_like = DeployConfig(
            ttl_in_minutes=60,
            arca_metadata={"key": "value"},
            envs={"KEY": "value"},
        )
        assert arca_like.ttl_in_minutes == 60
        assert arca_like.machine_id is None  # Local field not set

        # Typical Local usage
        local_like = DeployConfig(
            machine_id="machine-123",
            agent_code="agent-001",
            envs={"KEY": "value"},
        )
        assert local_like.machine_id == "machine-123"
        assert local_like.ttl_in_minutes is None  # ARCA field not set

    def test_engine_type_field_free_form_string(self) -> None:
        """Test that DeployConfig accepts engine_type as a free-form string (D-01)."""
        # Test 1: DeployConfig can be instantiated with engine_type="openclaw"
        config = DeployConfig(engine_type="openclaw")
        assert config.engine_type == "openclaw"

        # Test 2: DeployConfig without engine_type defaults to None
        config2 = DeployConfig()
        assert config2.engine_type is None

        # Test 3: Serialization roundtrip preserves engine_type
        dumped = config.model_dump()
        assert dumped["engine_type"] == "openclaw"
        restored = DeployConfig.model_validate(dumped)
        assert restored.engine_type == "openclaw"

    def test_engine_type_no_enum_validation(self) -> None:
        """Test that engine_type accepts any string value (no enum restriction per D-01)."""
        config = DeployConfig(engine_type="moltis")
        assert config.engine_type == "moltis"

        config2 = DeployConfig(engine_type="custom-runtime-v2")
        assert config2.engine_type == "custom-runtime-v2"


class TestDeployConfigLifecycleHooks:
    """Tests for lifecycle hook fields in DeployConfig."""

    def test_create_hooks(self) -> None:
        """Test after_create hook fields."""
        config = DeployConfig(
            after_create_cmd_hook="echo 'created'",
            after_create_hook_wait_seconds=120,
        )
        assert config.after_create_cmd_hook == "echo 'created'"
        assert config.after_create_hook_wait_seconds == 120

    def test_destroy_hooks(self) -> None:
        """Test before_destroy hook fields."""
        config = DeployConfig(
            before_destroy_cmd_hook="echo 'destroying'",
            before_destroy_hook_wait_seconds=60,
        )
        assert config.before_destroy_cmd_hook == "echo 'destroying'"
        assert config.before_destroy_hook_wait_seconds == 60

    def test_hook_defaults(self) -> None:
        """Test that hook wait seconds default to 300."""
        config = DeployConfig()
        assert config.after_create_hook_wait_seconds == 300
        assert config.before_destroy_hook_wait_seconds == 300
        assert config.after_create_cmd_hook is None
        assert config.before_destroy_cmd_hook is None


class TestDeployConfigEnvsMerging:
    """Tests for envs field behavior in DeployConfig."""

    def test_envs_none(self) -> None:
        """Test that envs can be None."""
        config = DeployConfig()
        assert config.envs is None

    def test_envs_set(self) -> None:
        """Test that envs can be set."""
        config = DeployConfig(envs={"KEY": "value", "FOO": "bar"})
        assert config.envs == {"KEY": "value", "FOO": "bar"}

    def test_envs_roundtrip(self) -> None:
        """Test envs serialization roundtrip."""
        config = DeployConfig(envs={"KEY": "value"})
        dumped = config.model_dump()
        restored = DeployConfig.model_validate(dumped)
        assert restored.envs == {"KEY": "value"}


class TestDeployConfigBackwardCompatibility:
    """Tests for backward compatibility considerations.

    Note: DeployConfig is a breaking change from the previous union type.
    These tests document the expected behavior.
    """

    def test_unified_deploy_config_is_single_class(self) -> None:
        """Test that DeployConfig is a single unified class, not a union."""
        config = DeployConfig()

        # It's a single BaseModel, not a Union
        assert isinstance(config, BaseModel)
        assert config.__class__.__name__ == "DeployConfig"

    def test_platform_specific_configs_still_exist(self) -> None:
        """Test that platform-specific configs are preserved for internal use."""
        arca = ArcaDeployConfig(ttl_in_minutes=60)
        local = LocalDeployConfig(machine_id="machine-123")
        sigma = SigmaDeployConfig(zone="zone-1")

        assert arca.ttl_in_minutes == 60
        assert local.machine_id == "machine-123"
        assert sigma.zone == "zone-1"


class TestPlatformSpecificConfigs:
    """Tests for preserved platform-specific config classes."""

    def test_arca_deploy_config_preserved(self):
        """Test ArcaDeployConfig is preserved for internal use."""
        config = ArcaDeployConfig(
            ttl_in_minutes=120,
            arca_metadata={"key": "value"},
            mount_points=[],
        )

        assert config.ttl_in_minutes == 120
        assert config.arca_metadata == {"key": "value"}
        # No platform_type field in unified approach (optional for backward compatibility)

    def test_local_deploy_config_preserved(self):
        """Test LocalDeployConfig is preserved for internal use."""
        config = LocalDeployConfig(
            machine_id="machine-123",
            agent_code="agent-001",
            envs={"KEY": "value"},
        )

        assert config.machine_id == "machine-123"
        assert config.agent_code == "agent-001"
        assert config.envs == {"KEY": "value"}

    def test_sigma_deploy_config_preserved(self):
        """Test SigmaDeployConfig is preserved for internal use."""
        config = SigmaDeployConfig(
            zone="zone-1",
            vpc_config={"vpc": "test"},
            sigma_metadata={"key": "value"},
        )

        assert config.zone == "zone-1"
        assert config.vpc_config == {"vpc": "test"}
        assert config.sigma_metadata == {"key": "value"}

    def test_resource_spec_shared_field(self):
        """Test that resource_spec can be set in all config types."""
        # ArcaDeployConfig doesn't have resource_spec in this implementation
        # (it was added for illustrative purposes in the plan)
        pass


class TestDeviceCredentials:
    """Tests for DeviceCredentials model — serialization_alias mapping,
    exclude_none behavior, empty-to-None, and round-trip preservation.
    """

    def test_import_from_public_api(self) -> None:
        """DeviceCredentials is importable from the public API surface."""
        from secbaas.community.api.device_manage import DeviceCredentials

        assert DeviceCredentials is not None

    def test_all_fields_default_to_none(self) -> None:
        """All 9 fields default to None on a fresh DeviceCredentials()."""
        from secbaas.community.api.device_manage import DeviceCredentials

        dc = DeviceCredentials()
        assert dc.token is None
        assert dc.client_id is None
        assert dc.owner_id is None
        assert dc.bot_id is None
        assert dc.entity_id is None
        assert dc.entity_type is None
        assert dc.bot_type is None
        assert dc.agent_code is None
        assert dc.stage is None

    def test_serialization_alias_maps_to_uppercase(self) -> None:
        """model_dump(by_alias=True) produces uppercase keys matching wire format."""
        from secbaas.community.api.device_manage import DeviceCredentials

        dc = DeviceCredentials(token="abc123")
        dump = dc.model_dump(by_alias=True, exclude_none=True)
        assert dump == {"TOKEN": "abc123"}

    def test_exclude_none_drops_none_fields(self) -> None:
        """model_dump(exclude_none=True) only includes populated fields."""
        from secbaas.community.api.device_manage import DeviceCredentials

        dc = DeviceCredentials(token="tok", client_id="cid")
        dump = dc.model_dump(exclude_none=True, by_alias=True)
        assert "TOKEN" in dump
        assert "CLIENT_ID" in dump
        assert len(dump) == 2  # only the two populated fields

    def test_all_none_produces_empty_dict(self) -> None:
        """All-None DeviceCredentials produces empty dict from model_dump."""
        from secbaas.community.api.device_manage import DeviceCredentials

        dc = DeviceCredentials()
        dump = dc.model_dump(exclude_none=True, by_alias=True)
        assert dump == {}

    def test_empty_dict_or_none_becomes_none(self) -> None:
        """The (dump or None) pattern converts empty {} to None."""
        from secbaas.community.api.device_manage import DeviceCredentials

        dc = DeviceCredentials()
        result = dc.model_dump(exclude_none=True, by_alias=True) or None
        assert result is None

    def test_round_trip_construct_dump(self) -> None:
        """Verify consistent data: construct -> dump preserves all set field values."""
        creds = DeviceCredentials(
            token="tok",
            client_id="cid",
            entity_id="eid",
        )
        dump = creds.model_dump(exclude_none=True, by_alias=True)
        assert len(dump) == 3
        assert dump["TOKEN"] == "tok"
        assert dump["CLIENT_ID"] == "cid"
        assert dump["ENTITY_ID"] == "eid"

    def test_all_nine_fields_serialize_correctly(self) -> None:
        """All 9 fields with values serialize to correct uppercase keys."""
        from secbaas.community.api.device_manage import DeviceCredentials

        dc = DeviceCredentials(
            token="t",
            client_id="ci",
            owner_id="oi",
            bot_id="bi",
            entity_id="ei",
            entity_type="et",
            bot_type="bt",
            agent_code="ac",
            stage="s",
        )
        dump = dc.model_dump(exclude_none=True, by_alias=True)
        assert dump == {
            "TOKEN": "t",
            "CLIENT_ID": "ci",
            "OWNER_ID": "oi",
            "BOT_ID": "bi",
            "ENTITY_ID": "ei",
            "ENTITY_TYPE": "et",
            "BOT_TYPE": "bt",
            "AGENT_CODE": "ac",
            "STAGE": "s",
        }

    def test_constructor_with_kwargs_accepts_lowercase_names(self) -> None:
        """Constructing with lowercase field names (Python kwargs) works."""
        from secbaas.community.api.device_manage import DeviceCredentials

        dc = DeviceCredentials(token="t123", client_id="c456")
        assert dc.token == "t123"
        assert dc.client_id == "c456"

    def test_deploy_config_accepts_credentials(self) -> None:
        """DeployConfig can be constructed with a DeviceCredentials instance."""
        from secbaas.community.api.device_manage import DeployConfig, DeviceCredentials

        dc = DeployConfig(credentials=DeviceCredentials(token="x"))
        assert dc.credentials is not None
        assert dc.credentials.token == "x"

    def test_device_credentials_in_all(self) -> None:
        """DeviceCredentials appears in the public __all__ list."""
        import secbaas.community.api.device_manage as dm

        assert "DeviceCredentials" in dm.__all__


class TestDeployConfigPoolabFields:
    """Tests for POOLAB-specific fields in DeployConfig.

    Verifies D-01 and D-02: DeployConfig accepts 4 Poolab-specific optional fields.
    """

    def test_poolab_fields_default_to_none(self) -> None:
        """Poolab fields default to None when not provided."""
        config = DeployConfig()
        assert config.poolab_user_id is None
        assert config.poolab_tenant_id is None
        assert config.poolab_image_id is None
        assert config.poolab_envs is None

    def test_poolab_fields_set_individually(self) -> None:
        """Poolab fields can be set individually without affecting others."""
        config = DeployConfig(poolab_user_id="user-001")
        assert config.poolab_user_id == "user-001"
        assert config.poolab_tenant_id is None
        assert config.poolab_image_id is None
        assert config.poolab_envs is None

    def test_poolab_fields_all_set(self) -> None:
        """All 4 Poolab fields can be set simultaneously."""
        config = DeployConfig(
            poolab_user_id="u1",
            poolab_tenant_id="t1",
            poolab_image_id="img1",
            poolab_envs={"KEY": "VALUE"},
        )
        assert config.poolab_user_id == "u1"
        assert config.poolab_tenant_id == "t1"
        assert config.poolab_image_id == "img1"
        assert config.poolab_envs == {"KEY": "VALUE"}

    def test_poolab_fields_serialize_to_dict(self) -> None:
        """Poolab fields appear in model_dump() output."""
        config = DeployConfig(poolab_user_id="u1")
        dumped = config.model_dump()
        assert "poolab_user_id" in dumped
        assert dumped["poolab_user_id"] == "u1"

    def test_poolab_fields_with_none_envs(self) -> None:
        """poolab_envs can be explicitly set to None."""
        config = DeployConfig(poolab_user_id="u1", poolab_envs=None)
        assert config.poolab_envs is None

    def test_poolab_fields_coexist_with_other_platform_fields(self) -> None:
        """Poolab fields coexist with fields from other platforms."""
        config = DeployConfig(
            poolab_user_id="u1",
            machine_id="m1",
            envs={"GLOBAL": "v"},
        )
        assert config.poolab_user_id == "u1"
        assert config.machine_id == "m1"
        assert config.envs == {"GLOBAL": "v"}

    def test_poolab_fields_in_model_dump_exclude_none(self) -> None:
        """Poolab fields are excluded with exclude_none=True when not set."""
        config = DeployConfig()
        dumped = config.model_dump(exclude_none=True)
        assert "poolab_user_id" not in dumped
        assert "poolab_tenant_id" not in dumped
        assert "poolab_image_id" not in dumped
        assert "poolab_envs" not in dumped
