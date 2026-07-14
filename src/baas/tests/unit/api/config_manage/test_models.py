"""Unit tests for api/config_manage/_models.py and _exceptions.py — system config domain types."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from secbaas.community.api.config_manage import (
    SystemConfigCreate,
    SystemConfigListResponse,
    SystemConfigNotFoundError,
    SystemConfigResponse,
    SystemConfigUpdate,
)

# ==================== SystemConfigCreate ====================


class TestSystemConfigCreate:
    """Tests for SystemConfigCreate model."""

    def test_required_fields(self):
        """WHEN all required fields provided, THEN model validates."""
        data = SystemConfigCreate(conf_key="my.key", name="My Config")
        assert data.conf_key == "my.key"
        assert data.name == "My Config"
        assert data.conf_value is None
        assert data.env is None
        assert data.description is None
        assert data.operator is None

    def test_conf_key_missing(self):
        """WHEN conf_key omitted, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            SystemConfigCreate(name="test")

    def test_name_missing(self):
        """WHEN name omitted, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            SystemConfigCreate(conf_key="k")

    def test_conf_key_pattern_valid(self):
        """WHEN conf_key matches pattern, THEN accepted."""
        keys = ["simple", "multi.part.key", "with_underscore", "a.b.c.d"]
        for key in keys:
            data = SystemConfigCreate(conf_key=key, name="test")
            assert data.conf_key == key

    def test_conf_key_pattern_invalid(self):
        """WHEN conf_key does not match pattern, THEN ValidationError."""
        keys = ["leading.dot.", ".leading", "spaces in key", "", "a..b"]
        for key in keys:
            with pytest.raises(ValidationError, match="conf_key"):
                SystemConfigCreate(conf_key=key, name="test")

    def test_conf_key_max_length(self):
        """WHEN conf_key exceeds 256 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            SystemConfigCreate(conf_key="a" * 257, name="test")

    def test_name_max_length(self):
        """WHEN name exceeds 256 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            SystemConfigCreate(conf_key="k", name="x" * 257)

    def test_description_max_length(self):
        """WHEN description exceeds 1024 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            SystemConfigCreate(conf_key="k", name="t", description="x" * 1025)

    def test_operator_max_length(self):
        """WHEN operator exceeds 64 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            SystemConfigCreate(conf_key="k", name="t", operator="x" * 65)

    def test_env_max_length(self):
        """WHEN env exceeds 32 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            SystemConfigCreate(conf_key="k", name="t", env="x" * 33)

    def test_all_optional_fields(self):
        """WHEN all optional fields provided, THEN they are stored."""
        data = SystemConfigCreate(
            conf_key="my.key",
            name="My Config",
            conf_value="value-123",
            env="prod",
            description="A test config",
            operator="user-1",
        )
        assert data.conf_value == "value-123"
        assert data.env == "prod"
        assert data.description == "A test config"
        assert data.operator == "user-1"


# ==================== SystemConfigUpdate ====================


class TestSystemConfigUpdate:
    """Tests for SystemConfigUpdate model."""

    def test_all_fields_optional(self):
        """WHEN created without args, THEN all fields are None."""
        data = SystemConfigUpdate()
        assert data.conf_value is None
        assert data.name is None
        assert data.description is None
        assert data.operator is None

    def test_partial_update_conf_value(self):
        """WHEN only conf_value provided, THEN only that field is set."""
        data = SystemConfigUpdate(conf_value="new-value")
        assert data.conf_value == "new-value"
        assert data.name is None

    def test_partial_update_name(self):
        """WHEN only name provided, THEN only that field is set."""
        data = SystemConfigUpdate(name="new-name")
        assert data.name == "new-name"
        assert data.conf_value is None

    def test_description_max_length(self):
        """WHEN description exceeds 1024 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            SystemConfigUpdate(description="x" * 1025)

    def test_operator_max_length(self):
        """WHEN operator exceeds 64 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            SystemConfigUpdate(operator="x" * 65)

    def test_name_max_length(self):
        """WHEN name exceeds 256 chars, THEN ValidationError."""
        with pytest.raises(ValidationError):
            SystemConfigUpdate(name="x" * 257)

    def test_all_fields(self):
        """WHEN all fields provided, THEN they are stored."""
        data = SystemConfigUpdate(
            conf_value="v", name="n", description="d", operator="o"
        )
        assert data.conf_value == "v"
        assert data.name == "n"
        assert data.description == "d"
        assert data.operator == "o"


# ==================== SystemConfigResponse ====================


class TestSystemConfigResponse:
    """Tests for SystemConfigResponse model."""

    def test_all_fields(self):
        """WHEN all fields provided, THEN model validates."""
        now = datetime.now()
        resp = SystemConfigResponse(
            id=1,
            conf_key="my.key",
            conf_value="val",
            env="prod",
            name="My Config",
            description="desc",
            creator="user-1",
            modifier="user-1",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.id == 1
        assert resp.conf_key == "my.key"
        assert resp.conf_value == "val"
        assert resp.env == "prod"
        assert resp.name == "My Config"
        assert resp.description == "desc"
        assert resp.creator == "user-1"
        assert resp.modifier == "user-1"
        assert resp.gmt_create == now
        assert resp.gmt_modified == now

    def test_nullable_fields(self):
        """WHEN nullable fields are None, THEN stored as None."""
        now = datetime.now()
        resp = SystemConfigResponse(
            id=1,
            conf_key="k",
            conf_value=None,
            env="dev",
            name="n",
            description=None,
            creator="u",
            modifier="u",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.conf_value is None
        assert resp.description is None

    def test_from_attributes(self):
        """THEN model_config has from_attributes=True."""
        assert SystemConfigResponse.model_config.get("from_attributes") is True

    def test_populate_by_name(self):
        """THEN model_config has populate_by_name=True."""
        assert SystemConfigResponse.model_config.get("populate_by_name") is True


# ==================== SystemConfigListResponse ====================


class TestSystemConfigListResponse:
    """Tests for SystemConfigListResponse model."""

    def test_default_empty(self):
        """WHEN created with defaults, THEN items is empty list."""
        resp = SystemConfigListResponse(items=[], total=0, page=1, page_size=20)
        assert resp.items == []
        assert resp.total == 0

    def test_with_items(self):
        """WHEN items provided, THEN they are stored."""
        now = datetime.now()
        item = SystemConfigResponse(
            id=1,
            conf_key="k",
            conf_value=None,
            env="dev",
            name="n",
            description=None,
            creator="u",
            modifier="u",
            gmt_create=now,
            gmt_modified=now,
        )
        resp = SystemConfigListResponse(items=[item], total=1, page=1, page_size=20)
        assert len(resp.items) == 1
        assert resp.items[0].conf_key == "k"


# ==================== SystemConfigNotFoundError ====================


class TestSystemConfigNotFoundError:
    """Tests for SystemConfigNotFoundError exception."""

    def test_with_conf_key(self):
        """WHEN created with conf_key, THEN message includes key."""
        err = SystemConfigNotFoundError(conf_key="my.key")
        assert err.error_code == "CONFIG_NOT_FOUND"
        assert err.http_status == 404
        assert "my.key" in err.message
        assert "my.key" in str(err)

    def test_empty_key(self):
        """WHEN created with empty key, THEN message still works."""
        err = SystemConfigNotFoundError()
        assert err.message == "System config not found: "

    def test_domain_error_subclass(self):
        """THEN SystemConfigNotFoundError is a DomainError subclass."""
        from secbaas.community.api import DomainError

        assert issubclass(SystemConfigNotFoundError, DomainError)
