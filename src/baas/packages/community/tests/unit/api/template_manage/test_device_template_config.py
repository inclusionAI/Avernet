"""Unit tests for DeviceTemplateConfig type alias and its implementations."""

from __future__ import annotations

from secbaas.api.template_manage import (
    ArcaTemplateConfig,
    PoolabTemplateConfig,
    SigmaTemplateConfig,
)


class TestArcaTemplateConfig:
    """Tests for ArcaTemplateConfig model validation."""

    def test_with_required_fields(self) -> None:
        """Test creating ArcaTemplateConfig with required fields."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://arca.example.com",
                "api_key": "test-key",
            }
        )
        assert config.type == "ARCA"
        assert config.base_url == "https://arca.example.com"
        assert config.api_key == "test-key"

    def test_with_all_fields(self) -> None:
        """Test creating ArcaTemplateConfig with all fields."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://arca.example.com",
                "api_key": "test-key",
                "arca_template_id": "tpl-123",
                "arca_template_id_pre": "tpl-123-pre",
                "arca_template_id_prod": "tpl-123-prod",
                "oss_mount_id": "mount-456",
                "default_ttl_minutes": 10080,
            }
        )
        assert config.base_url == "https://arca.example.com"
        assert config.api_key == "test-key"
        assert config.arca_template_id == "tpl-123"
        assert config.arca_template_id_pre == "tpl-123-pre"
        assert config.arca_template_id_prod == "tpl-123-prod"
        assert config.oss_mount_id == "mount-456"
        assert config.default_ttl_minutes == 10080

    def test_with_optional_fields(self) -> None:
        """Test creating ArcaTemplateConfig with optional fields."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "app_name": "custom-app",
                "timeout": 60.0,
            }
        )
        assert config.base_url == "https://example.com"
        assert config.app_name == "custom-app"
        assert config.timeout == 60.0

    def test_model_dump_includes_fields(self) -> None:
        """Test that model_dump includes fields."""
        config = ArcaTemplateConfig.model_validate(
            {"type": "ARCA", "base_url": "https://example.com", "api_key": "key"}
        )
        dumped = config.model_dump()
        assert dumped["base_url"] == "https://example.com"
        assert "type" in dumped
        assert "api_key" in dumped

    def test_model_validate_with_all_fields(self) -> None:
        """Test model_validate accepts all fields."""
        data = {
            "type": "ARCA",
            "base_url": "https://arca.example.com",
            "api_key": "test-key",
            "arca_template_id": "tpl-123",
        }
        config = ArcaTemplateConfig.model_validate(data)
        assert config.base_url == "https://arca.example.com"
        assert config.arca_template_id == "tpl-123"


class TestArcaTemplateConfigExtraAllow:
    """Tests for ArcaTemplateConfig extra='allow' behavior."""

    def test_unknown_keys_preserved(self) -> None:
        """Test that unknown keys are preserved."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "custom_field": "some_value",
            }
        )
        assert config.base_url == "https://example.com"
        assert config.model_dump()["custom_field"] == "some_value"

    def test_model_dump_includes_extra_fields(self) -> None:
        """Test that model_dump includes extra fields."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "custom_key": "custom_value",
            }
        )
        dumped = config.model_dump()
        assert dumped["base_url"] == "https://example.com"
        assert dumped["custom_key"] == "custom_value"

    def test_model_validate_with_extra_fields(self) -> None:
        """Test that model_validate accepts extra fields."""
        data = {
            "type": "ARCA",
            "base_url": "https://example.com",
            "api_key": "key",
            "extra_field": "extra_value",
        }
        config = ArcaTemplateConfig.model_validate(data)
        assert config.base_url == "https://example.com"
        assert config.model_dump()["extra_field"] == "extra_value"

    def test_round_trip_with_extra_fields(self) -> None:
        """Test serialization and deserialization round trip."""
        original = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "custom_field": {"nested": "data"},
            }
        )
        dumped = original.model_dump()
        restored = ArcaTemplateConfig.model_validate(dumped)
        assert restored.base_url == original.base_url
        assert (
            restored.model_dump()["custom_field"]
            == original.model_dump()["custom_field"]
        )


class TestArcaTemplateConfigSerialization:
    """Tests for ArcaTemplateConfig serialization for storage."""

    def test_model_dump_exclude_none(self) -> None:
        """Test model_dump with exclude_none for storage."""
        config = ArcaTemplateConfig.model_validate(
            {"type": "ARCA", "base_url": "https://example.com", "api_key": "key"}
        )
        dumped = config.model_dump(exclude_none=True)
        assert "base_url" in dumped
        assert "type" in dumped
        assert "api_key" in dumped
        # arca_template_id, oss_mount_id should be excluded (None)
        assert "arca_template_id" not in dumped


class TestArcaTemplateConfigEnvironmentSelection:
    """Tests for get_effective_template_id environment-aware selection."""

    def test_get_effective_template_id_default(self) -> None:
        """Test default template_id for non-pre/prod envs."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "arca_template_id": "default-id",
            }
        )
        assert config.get_effective_template_id("dev") == "default-id"
        assert config.get_effective_template_id("test") == "default-id"
        assert config.get_effective_template_id("staging") == "default-id"

    def test_get_effective_template_id_pre_with_value(self) -> None:
        """Test pre environment uses arca_template_id_pre when set."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "arca_template_id": "default-id",
                "arca_template_id_pre": "pre-id",
            }
        )
        assert config.get_effective_template_id("pre") == "pre-id"

    def test_get_effective_template_id_pre_fallback(self) -> None:
        """Test pre environment falls back to arca_template_id when pre not set."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "arca_template_id": "default-id",
            }
        )
        assert config.get_effective_template_id("pre") == "default-id"

    def test_get_effective_template_id_prod_with_value(self) -> None:
        """Test prod environment uses arca_template_id_prod when set."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "arca_template_id": "default-id",
                "arca_template_id_prod": "prod-id",
            }
        )
        assert config.get_effective_template_id("prod") == "prod-id"

    def test_get_effective_template_id_prod_fallback(self) -> None:
        """Test prod environment falls back to arca_template_id when prod not set."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "arca_template_id": "default-id",
            }
        )
        assert config.get_effective_template_id("prod") == "default-id"

    def test_get_effective_template_id_case_insensitive(self) -> None:
        """Test environment matching is case-insensitive."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "arca_template_id": "default-id",
                "arca_template_id_pre": "pre-id",
                "arca_template_id_prod": "prod-id",
            }
        )
        assert config.get_effective_template_id("PRE") == "pre-id"
        assert config.get_effective_template_id("Pre") == "pre-id"
        assert config.get_effective_template_id("PROD") == "prod-id"
        assert config.get_effective_template_id("Prod") == "prod-id"

    def test_get_effective_template_id_all_envs_set(self) -> None:
        """Test when all environment template IDs are configured."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "arca_template_id": "default-id",
                "arca_template_id_pre": "pre-id",
                "arca_template_id_prod": "prod-id",
            }
        )
        assert config.get_effective_template_id("pre") == "pre-id"
        assert config.get_effective_template_id("prod") == "prod-id"
        assert config.get_effective_template_id("dev") == "default-id"


class TestArcaTemplateConfigAlias:
    """Tests for template_id alias backward compatibility."""

    def test_alias_template_id_to_arca_template_id(self) -> None:
        """Test that 'template_id' in JSON maps to arca_template_id via alias."""
        data = {
            "type": "ARCA",
            "base_url": "https://example.com",
            "api_key": "key",
            "template_id": "legacy-id",
        }
        config = ArcaTemplateConfig.model_validate(data)
        assert config.arca_template_id == "legacy-id"

    def test_model_dump_by_alias(self) -> None:
        """Test that model_dump with by_alias includes template_id."""
        config = ArcaTemplateConfig.model_validate(
            {
                "type": "ARCA",
                "base_url": "https://example.com",
                "api_key": "key",
                "arca_template_id": "my-template-id",
            }
        )
        dumped = config.model_dump(by_alias=True)
        assert "template_id" in dumped
        assert dumped["template_id"] == "my-template-id"

    def test_backward_compatibility_round_trip(self) -> None:
        """Test round-trip: old JSON -> model -> old JSON."""
        old_json = {
            "type": "ARCA",
            "base_url": "https://example.com",
            "api_key": "key",
            "template_id": "tpl-123",
        }
        config = ArcaTemplateConfig.model_validate(old_json)
        dumped = config.model_dump(by_alias=True, exclude_none=True)
        assert dumped["template_id"] == "tpl-123"


class TestSigmaTemplateConfig:
    """Tests for SigmaTemplateConfig model validation."""

    def test_with_required_fields(self) -> None:
        """Test creating SigmaTemplateConfig with required fields."""
        config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://sigma.example.com",
            access_key="ak",
            secret_key="sk",
        )
        assert config.type == "Sigma"
        assert config.endpoint == "https://sigma.example.com"
        assert config.access_key == "ak"
        assert config.secret_key == "sk"

    def test_with_all_fields(self) -> None:
        """Test creating SigmaTemplateConfig with all fields."""
        config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://sigma.example.com",
            access_key="ak",
            secret_key="sk",
            region="us-east-1",
        )
        assert config.endpoint == "https://sigma.example.com"
        assert config.access_key == "ak"
        assert config.secret_key == "sk"
        assert config.region == "us-east-1"

    def test_model_dump_includes_fields(self) -> None:
        """Test that model_dump includes fields."""
        config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://example.com",
            access_key="ak",
            secret_key="sk",
        )
        dumped = config.model_dump()
        assert dumped["endpoint"] == "https://example.com"
        assert "type" in dumped
        assert "secret_key" in dumped


class TestPoolabTemplateConfig:
    """Tests for PoolabTemplateConfig model validation."""

    def test_with_required_fields(self) -> None:
        """Test creating PoolabTemplateConfig with required fields."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint_pre": "https://poolab-pre.alipay.com",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
            }
        )
        assert config.type == "POOLAB"
        assert config.poolab_endpoint_pre == "https://poolab-pre.alipay.com"
        assert config.poolab_endpoint_prod is None
        assert config.poolab_tenant_id == "tenant-001"
        assert config.poolab_tenant_token == "token-abc123"
        assert config.encrypt_tenant_token is False
        assert config.poolab_default_image_id_pre is None
        assert config.poolab_default_image_id_prod is None

    def test_with_all_fields(self) -> None:
        """Test creating PoolabTemplateConfig with all fields including optional."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint_pre": "https://poolab-pre.alipay.com",
                "poolab_endpoint_prod": "https://poolab-prod.alipay.com",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
                "encrypt_tenant_token": True,
                "poolab_default_image_id_pre": "img-poolab-1",
            }
        )
        assert config.encrypt_tenant_token is True
        assert config.poolab_default_image_id_pre == "img-poolab-1"

    def test_model_dump_round_trip(self) -> None:
        """Test that model_dump -> model_validate preserves all fields."""
        original = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint_pre": "https://poolab-pre.alipay.com",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
                "encrypt_tenant_token": True,
                "poolab_default_image_id_pre": "img-poolab-1",
            }
        )
        dumped = original.model_dump()
        assert dumped["type"] == "POOLAB"
        assert dumped["poolab_endpoint_pre"] == "https://poolab-pre.alipay.com"
        assert "poolab_endpoint" not in dumped
        assert "poolab_default_image_id" not in dumped
        restored = PoolabTemplateConfig.model_validate(dumped)
        assert restored.poolab_tenant_id == original.poolab_tenant_id
        assert restored.encrypt_tenant_token == original.encrypt_tenant_token
        assert (
            restored.poolab_default_image_id_pre == original.poolab_default_image_id_pre
        )

    def test_encrypt_flag_default(self) -> None:
        """Test encrypt_tenant_token defaults to False."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint_pre": "https://poolab-pre.alipay.com",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
            }
        )
        assert config.encrypt_tenant_token is False

    def test_no_resource_spec(self) -> None:
        """Test model_dump output does NOT contain resource_spec field."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint_pre": "https://poolab-pre.alipay.com",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
            }
        )
        dumped = config.model_dump()
        assert "resource_spec" not in dumped


class TestPoolabTemplateConfigEnvironmentSelection:
    """Tests for PoolabTemplateConfig env-aware endpoint and image_id selection."""

    def test_get_effective_endpoint_pre(self) -> None:
        """Test get_effective_endpoint selects correct env-specific endpoint."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint_pre": "https://poolab-pre.alipay.com",
                "poolab_endpoint_prod": "https://poolab-prod.alipay.com",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
            }
        )
        assert config.get_effective_endpoint("pre") == "https://poolab-pre.alipay.com"
        assert config.get_effective_endpoint("prod") == "https://poolab-prod.alipay.com"
        assert config.get_effective_endpoint("dev") is None

    def test_get_effective_endpoint_unconfigured_env(self) -> None:
        """Test get_effective_endpoint returns None for unconfigured env (no fallback)."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint_pre": "https://poolab-pre.alipay.com",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
            }
        )
        assert config.get_effective_endpoint("pre") == "https://poolab-pre.alipay.com"
        assert config.get_effective_endpoint("prod") is None

    def test_get_effective_endpoint_case_insensitive(self) -> None:
        """Test get_effective_endpoint with case-insensitive env matching."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint_pre": "https://poolab-pre.alipay.com",
                "poolab_endpoint_prod": "https://poolab-prod.alipay.com",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
            }
        )
        assert config.get_effective_endpoint("PRE") == "https://poolab-pre.alipay.com"
        assert config.get_effective_endpoint("Prod") == "https://poolab-prod.alipay.com"

    def test_get_effective_endpoint_dev_test_sim(self) -> None:
        """Test get_effective_endpoint returns None for dev/test/sim envs."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint_pre": "https://poolab-pre.alipay.com",
                "poolab_endpoint_prod": "https://poolab-prod.alipay.com",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
            }
        )
        assert config.get_effective_endpoint("dev") is None
        assert config.get_effective_endpoint("test") is None
        assert config.get_effective_endpoint("sim") is None

    def test_get_effective_image_id_pre(self) -> None:
        """Test get_effective_image_id selects correct env-specific image ID."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
                "poolab_default_image_id_pre": "img-pre",
                "poolab_default_image_id_prod": "img-prod",
            }
        )
        assert config.get_effective_image_id("pre") == "img-pre"
        assert config.get_effective_image_id("prod") == "img-prod"
        assert config.get_effective_image_id("dev") is None

    def test_get_effective_image_id_unconfigured_env(self) -> None:
        """Test get_effective_image_id returns None for unconfigured env (no fallback)."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
                "poolab_default_image_id_pre": "img-pre",
            }
        )
        assert config.get_effective_image_id("prod") is None

    def test_get_effective_image_id_case_insensitive(self) -> None:
        """Test get_effective_image_id with case-insensitive env matching."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
                "poolab_default_image_id_pre": "img-pre",
                "poolab_default_image_id_prod": "img-prod",
            }
        )
        assert config.get_effective_image_id("PRE") == "img-pre"
        assert config.get_effective_image_id("Prod") == "img-prod"

    def test_getters_both_fully_configured(self) -> None:
        """Test both getters return correct values when all 4 fields are configured."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint_pre": "https://poolab-pre.alipay.com",
                "poolab_endpoint_prod": "https://poolab-prod.alipay.com",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
                "poolab_default_image_id_pre": "img-pre",
                "poolab_default_image_id_prod": "img-prod",
            }
        )
        assert config.get_effective_endpoint("pre") == "https://poolab-pre.alipay.com"
        assert config.get_effective_endpoint("prod") == "https://poolab-prod.alipay.com"
        assert config.get_effective_image_id("pre") == "img-pre"
        assert config.get_effective_image_id("prod") == "img-prod"

    def test_old_field_names_extra_allow(self) -> None:
        """Test old field names deserialize silently (extra='allow')."""
        config = PoolabTemplateConfig.model_validate(
            {
                "type": "POOLAB",
                "poolab_endpoint": "https://poolab-pre.alipay.com",
                "poolab_default_image_id": "img-old",
                "poolab_tenant_id": "tenant-001",
                "poolab_tenant_token": "token-abc123",
            }
        )
        assert config.poolab_endpoint_pre is None
        assert config.poolab_endpoint_prod is None
        assert config.poolab_default_image_id_pre is None
        assert config.poolab_default_image_id_prod is None
