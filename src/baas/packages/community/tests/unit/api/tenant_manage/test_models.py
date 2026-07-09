"""Unit tests for api/domain/tenant_manage.py — tenant domain types."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from secbaas.api.tenant_manage import (
    TenantConfig,
    TenantCreate,
    TenantListResponse,
    TenantNotFoundError,
    TenantResponse,
    TenantType,
    TenantUpdate,
)

# ==================== TenantType ====================


class TestTenantType:
    """Tests for the TenantType enum."""

    def test_members(self):
        """THEN all expected members exist with correct values."""
        assert TenantType.SIGMA == "Sigma"
        assert TenantType.ARCA == "ARCA"
        assert TenantType.LOCAL == "Local"
        assert TenantType.POOLAB == "Poolab"

    def test_from_string(self):
        """WHEN constructing from string, THEN correct member returned."""
        assert TenantType("Sigma") is TenantType.SIGMA
        assert TenantType("ARCA") is TenantType.ARCA

    def test_poolab_member(self):
        """THEN Poolab member exists with correct value and string construction."""
        assert TenantType.POOLAB == "Poolab"
        assert TenantType("Poolab") is TenantType.POOLAB

    def test_invalid_enum_value(self):
        """WHEN constructing from invalid string, THEN ValueError raised."""
        with pytest.raises(ValueError):
            TenantType("INVALID")


# ==================== TenantConfig ====================


class TestTenantConfig:
    """Tests for TenantConfig model."""

    def test_empty_config(self):
        """WHEN created without args, THEN all fields are None."""
        config = TenantConfig()
        assert config.default_template_uuid is None

    def test_with_template_uuid(self):
        """WHEN created with default_template_uuid, THEN it is stored."""
        config = TenantConfig(default_template_uuid="uuid-123")
        assert config.default_template_uuid == "uuid-123"

    def test_extra_allow(self):
        """WHEN unknown fields provided, THEN they are accepted (extra='allow')."""
        config = TenantConfig.model_validate(
            {"default_template_uuid": "uuid-123", "legacy_key": "old_value"}
        )
        assert config.default_template_uuid == "uuid-123"
        assert config.model_dump().get("legacy_key") == "old_value"

    def test_model_dump_exclude_none(self):
        """WHEN model_dump with exclude_none, THEN None fields omitted."""
        config = TenantConfig()
        dumped = config.model_dump(exclude_none=True)
        assert "default_template_uuid" not in dumped

    def test_model_dump_with_value(self):
        """WHEN model_dump with value, THEN non-None fields included."""
        config = TenantConfig(default_template_uuid="uuid-123")
        dumped = config.model_dump(exclude_none=True)
        assert dumped == {"default_template_uuid": "uuid-123"}


# ==================== TenantCreate ====================


class TestTenantCreate:
    """Tests for TenantCreate model."""

    def test_required_name(self):
        """WHEN name provided, THEN model validates."""
        data = TenantCreate(name="test-tenant")
        assert data.name == "test-tenant"

    def test_name_required(self):
        """WHEN name omitted, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            TenantCreate()

    def test_name_min_length(self):
        """WHEN name is empty string, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            TenantCreate(name="")

    def test_name_max_length(self):
        """WHEN name exceeds 256 chars, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            TenantCreate(name="x" * 257)

    def test_optional_fields_default_none(self):
        """WHEN only name provided, THEN optional fields default to None."""
        data = TenantCreate(name="test-tenant")
        assert data.description is None
        assert data.extra_config is None
        assert data.operator is None

    def test_with_all_fields(self):
        """WHEN all fields provided, THEN they are stored."""
        config = TenantConfig(default_template_uuid="uuid-123")
        data = TenantCreate(
            name="test-tenant",
            description="A test tenant",
            extra_config=config,
            operator="user-1",
        )
        assert data.description == "A test tenant"
        assert data.extra_config.default_template_uuid == "uuid-123"
        assert data.operator == "user-1"

    def test_description_max_length(self):
        """WHEN description exceeds 1024 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TenantCreate(name="t", description="x" * 1025)

    def test_operator_min_length(self):
        """WHEN operator is empty string, THEN ValidationError."""
        with pytest.raises(ValidationError):
            TenantCreate(name="t", operator="")


# ==================== TenantUpdate ====================


class TestTenantUpdate:
    """Tests for TenantUpdate model."""

    def test_all_fields_optional(self):
        """WHEN created without args, THEN all fields are None."""
        data = TenantUpdate()
        assert data.description is None
        assert data.extra_config is None
        assert data.operator is None

    def test_partial_update(self):
        """WHEN only description provided, THEN only that field is set."""
        data = TenantUpdate(description="new desc")
        assert data.description == "new desc"
        assert data.extra_config is None
        assert data.operator is None

    def test_with_config(self):
        """WHEN extra_config provided, THEN it is stored."""
        config = TenantConfig(default_template_uuid="uuid-456")
        data = TenantUpdate(extra_config=config)
        assert data.extra_config.default_template_uuid == "uuid-456"


# ==================== TenantResponse ====================


class TestTenantResponse:
    """Tests for TenantResponse model."""

    def test_all_fields(self):
        """WHEN all fields provided, THEN model validates."""
        now = datetime.now()
        resp = TenantResponse(
            name="test-tenant",
            description="desc",
            env="prod",
            extra_config=None,
            creator="user-1",
            modifier="user-1",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.name == "test-tenant"
        assert resp.env == "prod"
        assert resp.gmt_create == now

    def test_from_attributes(self):
        """THEN model_config has from_attributes=True."""
        assert TenantResponse.model_config.get("from_attributes") is True

    def test_populate_by_name(self):
        """THEN model_config has populate_by_name=True."""
        assert TenantResponse.model_config.get("populate_by_name") is True

    def test_with_config(self):
        """WHEN extra_config provided, THEN it is stored."""
        now = datetime.now()
        config = TenantConfig(default_template_uuid="uuid-789")
        resp = TenantResponse(
            name="t",
            description=None,
            env="dev",
            extra_config=config,
            creator="u",
            modifier="u",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.extra_config.default_template_uuid == "uuid-789"


# ==================== TenantListResponse ====================


class TestTenantListResponse:
    """Tests for TenantListResponse model."""

    def test_default_empty(self):
        """WHEN created with defaults, THEN items is empty list."""
        resp = TenantListResponse(items=[], total=0, page=1, page_size=20)
        assert resp.items == []
        assert resp.total == 0

    def test_with_items(self):
        """WHEN items provided, THEN they are stored."""
        now = datetime.now()
        item = TenantResponse(
            name="t",
            description=None,
            env="dev",
            extra_config=None,
            creator="u",
            modifier="u",
            gmt_create=now,
            gmt_modified=now,
        )
        resp = TenantListResponse(items=[item], total=1, page=1, page_size=20)
        assert len(resp.items) == 1
        assert resp.items[0].name == "t"


# ==================== TenantNotFoundError ====================


class TestTenantNotFoundError:
    """Tests for TenantNotFoundError exception."""

    def test_default_message(self):
        """WHEN created with name, THEN message includes name."""
        err = TenantNotFoundError(name="my-tenant")
        assert err.error_code == "TENANT_NOT_FOUND"
        assert err.http_status == 404
        assert "my-tenant" in err.message
        assert "my-tenant" in str(err)

    def test_empty_name(self):
        """WHEN created with empty name, THEN message still works."""
        err = TenantNotFoundError()
        assert err.message == "Tenant not found: "
