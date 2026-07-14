"""Unit tests for TenantConfig model."""

from __future__ import annotations

from secbaas.community.api.tenant_manage import TenantConfig


class TestTenantConfig:
    """Tests for TenantConfig model validation."""

    def test_empty_config(self) -> None:
        """Test creating empty TenantConfig."""
        config = TenantConfig.model_validate({})
        assert config.default_template_uuid is None

    def test_with_default_template_uuid(self) -> None:
        """Test creating TenantConfig with default_template_uuid."""
        config = TenantConfig(
            default_template_uuid="tpl-abc-123",
        )
        assert config.default_template_uuid == "tpl-abc-123"

    def test_partial_fields(self) -> None:
        """Test creating TenantConfig with partial fields."""
        config = TenantConfig(
            default_template_uuid="tpl-test-001",
        )
        assert config.default_template_uuid == "tpl-test-001"

    def test_extra_fields_allowed(self) -> None:
        """Test that extra fields are allowed (via model_validate)."""
        config = TenantConfig.model_validate(
            {"extra_field": "extra_value"},
        )
        assert config.model_dump()["extra_field"] == "extra_value"


class TestTenantConfigExtraAllow:
    """Tests for TenantConfig extra="allow" behavior."""

    def test_unknown_keys_preserved(self) -> None:
        """Test that unknown keys are preserved."""
        config = TenantConfig.model_validate(
            {
                "default_template_uuid": "tpl-001",
                "unknown_field": "some_value",
                "another_unknown": 123,
            }
        )
        assert config.default_template_uuid == "tpl-001"
        assert config.model_dump()["unknown_field"] == "some_value"
        assert config.model_dump()["another_unknown"] == 123

    def test_model_dump_includes_extra_fields(self) -> None:
        """Test that model_dump includes extra fields."""
        config = TenantConfig.model_validate(
            {
                "default_template_uuid": "tpl-001",
                "custom_key": "custom_value",
            }
        )
        dumped = config.model_dump()
        assert dumped["default_template_uuid"] == "tpl-001"
        assert dumped["custom_key"] == "custom_value"

    def test_model_validate_with_extra_fields(self) -> None:
        """Test that model_validate accepts extra fields."""
        data = {
            "default_template_uuid": "tpl-001",
            "extra_field": "extra_value",
            "legacy_field": "legacy_value",
        }
        config = TenantConfig.model_validate(data)
        assert config.default_template_uuid == "tpl-001"
        assert config.model_dump()["extra_field"] == "extra_value"
        assert config.model_dump()["legacy_field"] == "legacy_value"

    def test_round_trip_with_extra_fields(self) -> None:
        """Test serialization and deserialization round trip."""
        original = TenantConfig.model_validate(
            {
                "default_template_uuid": "tpl-001",
                "custom_field": {"nested": "data"},
            }
        )
        dumped = original.model_dump()
        restored = TenantConfig.model_validate(dumped)
        assert restored.default_template_uuid == original.default_template_uuid
        assert (
            restored.model_dump()["custom_field"]
            == original.model_dump()["custom_field"]
        )


class TestTenantConfigSerialization:
    """Tests for TenantConfig serialization for storage."""

    def test_model_dump_exclude_none(self) -> None:
        """Test model_dump with exclude_none for storage."""
        config = TenantConfig.model_validate(
            {
                "default_template_uuid": "tpl-001",
                "extra_field": "extra",
            }
        )
        dumped = config.model_dump(exclude_none=True)
        assert "default_template_uuid" in dumped
        assert "extra_field" in dumped

    def test_model_dump_exclude_none_keeps_empty_string(self) -> None:
        """Test that exclude_none keeps empty string values."""
        config = TenantConfig(default_template_uuid="")
        dumped = config.model_dump(exclude_none=True)
        assert "default_template_uuid" in dumped
        assert dumped.get("default_template_uuid") == ""
