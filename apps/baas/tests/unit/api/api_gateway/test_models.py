"""Unit tests for api_gateway models and exceptions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from secbaas.community.api.api_gateway import (
    APIKeyCreate,
    APIKeyCreateResponse,
    APIKeyError,
    APIKeyListResponse,
    APIKeyQuery,
    APIKeyRecord,
    APIKeyResponse,
    APIKeyStatus,
    APIKeyUpdate,
    AppAPIKeyCreate,
    BotAPIKeyCreate,
)


class TestAPIKeyStatus:
    """Test APIKeyStatus enum."""

    def test_values(self):
        assert APIKeyStatus.ACTIVE == "ACTIVE"
        assert APIKeyStatus.INACTIVE == "INACTIVE"
        assert APIKeyStatus.REVOKED == "REVOKED"

    def test_str_enum(self):
        assert str(APIKeyStatus.ACTIVE) == "ACTIVE"

    def test_membership(self):
        assert "ACTIVE" in APIKeyStatus._value2member_map_


class TestAPIKeyError:
    """Test APIKeyError exception."""

    def test_default(self):
        err = APIKeyError(code=400, message="bad request")
        assert err.code == 400
        assert err.message == "bad request"
        assert str(err) == "bad request"

    def test_not_domain_error(self):
        """APIKeyError extends Exception directly, not DomainError."""
        from secbaas.community.api import DomainError

        assert not issubclass(APIKeyError, DomainError)


class TestAPIKeyRecord:
    """Test APIKeyRecord dataclass."""

    def test_full_record(self):
        now = datetime.now(UTC)
        rec = APIKeyRecord(
            id=1,
            gmt_create=now,
            gmt_modified=now,
            api_key_hash="abc123",
            api_key_prefix="sk-",
            key_name="test-key",
            app_id="app-1",
            app_type="web",
            description="test",
            rate_limit_rpm=100,
            rate_limit_rpd=1000,
            status="ACTIVE",
            owner="user-1",
            tenant="default",
            env="dev",
            creator="user-1",
            modifier="user-1",
            policy=None,
        )
        assert rec.id == 1
        assert rec.api_key_prefix == "sk-"
        assert rec.key_name == "test-key"

    def test_full_record_all_fields(self):
        now = datetime.now(UTC)
        rec = APIKeyRecord(
            id=2,
            gmt_create=now,
            gmt_modified=now,
            api_key_hash="def456",
            api_key_prefix="sk-",
            key_name=None,
            app_id="app-2",
            app_type=None,
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="user-2",
            tenant=None,
            env="prod",
            creator="user-2",
            modifier=None,
            policy=None,
        )
        assert rec.key_name is None
        assert rec.description is None
        assert rec.modifier is None


class TestAPIKeyCreate:
    """Test APIKeyCreate model."""

    def test_required_only(self):
        model = APIKeyCreate(app_id="app-1")
        assert model.app_id == "app-1"
        assert model.key_name is None
        assert model.rate_limit_rpm is None

    def test_all_fields(self):
        model = APIKeyCreate(
            app_id="app-1",
            app_type="web",
            key_name="my-key",
            description="test key",
            rate_limit_rpm=100,
            rate_limit_rpd=1000,
            owner="user-1",
            tenant="default",
            policy='{"perm": "read"}',
        )
        assert model.app_type == "web"
        assert model.rate_limit_rpm == 100
        assert model.policy == '{"perm": "read"}'

    def test_app_id_min_length(self):
        with pytest.raises(ValidationError):
            APIKeyCreate(app_id="")

    def test_app_id_max_length(self):
        with pytest.raises(ValidationError):
            APIKeyCreate(app_id="x" * 129)

    def test_owner_max_length(self):
        with pytest.raises(ValidationError):
            APIKeyCreate(app_id="app-1", owner="x" * 65)


class TestAPIKeyUpdate:
    """Test APIKeyUpdate model."""

    def test_all_optional(self):
        model = APIKeyUpdate()
        assert model.key_name is None
        assert model.description is None

    def test_partial_update(self):
        model = APIKeyUpdate(key_name="renamed")
        assert model.key_name == "renamed"


class TestBotAPIKeyCreate:
    """Test BotAPIKeyCreate model."""

    def test_required_only(self):
        model = BotAPIKeyCreate(app_id="bot-1:user-001")
        assert model.app_id == "bot-1:user-001"
        assert model.key_name is None
        assert model.tenant == "team_claw"
        assert not hasattr(model, "app_type")
        assert not hasattr(model, "policy")

    def test_all_fields(self):
        model = BotAPIKeyCreate(
            app_id="bot-1:user-001",
            key_name="bot-key",
            description="a bot key",
            rate_limit_rpm=100,
            rate_limit_rpd=5000,
            tenant="custom-tenant",
        )
        assert model.key_name == "bot-key"
        assert model.rate_limit_rpm == 100
        assert model.tenant == "custom-tenant"

    def test_tenant_default(self):
        model = BotAPIKeyCreate(app_id="bot-1:user-001")
        assert model.tenant == "team_claw"

    def test_app_id_min_length(self):
        with pytest.raises(ValidationError):
            BotAPIKeyCreate(app_id="")

    def test_app_id_max_length(self):
        with pytest.raises(ValidationError):
            BotAPIKeyCreate(app_id="x" * 129)

    def test_no_app_type_field(self):
        model = BotAPIKeyCreate(app_id="bot-1:user-001")
        assert "app_type" not in AppAPIKeyCreate.model_fields

    def test_no_policy_field(self):
        model = BotAPIKeyCreate(app_id="bot-1:user-001")
        assert "policy" not in BotAPIKeyCreate.model_fields

    def test_no_owner_field(self):
        """BotAPIKeyCreate should not have owner field — it is set server-side."""
        assert "owner" not in BotAPIKeyCreate.model_fields


class TestAppAPIKeyCreate:
    """Test AppAPIKeyCreate model."""

    def test_required_fields(self):
        model = AppAPIKeyCreate(app_id="app-1")
        assert model.app_id == "app-1"
        assert model.key_name is None
        assert model.tenant == "team_claw"
        assert not hasattr(model, "app_type")

    def test_all_fields(self):
        model = AppAPIKeyCreate(
            app_id="app-1",
            key_name="app-key",
            description="an app key",
            rate_limit_rpm=200,
            rate_limit_rpd=10000,
            tenant="custom-tenant",
        )
        assert model.key_name == "app-key"
        assert model.rate_limit_rpm == 200
        assert model.tenant == "custom-tenant"

    def test_tenant_default(self):
        model = AppAPIKeyCreate(app_id="app-1")
        assert model.tenant == "team_claw"

    def test_app_id_min_length(self):
        with pytest.raises(ValidationError):
            AppAPIKeyCreate(app_id="")

    def test_app_id_max_length(self):
        with pytest.raises(ValidationError):
            AppAPIKeyCreate(app_id="x" * 129)

    def test_no_app_type_field(self):
        model = AppAPIKeyCreate(app_id="app-1")
        assert "app_type" not in AppAPIKeyCreate.model_fields

    def test_no_owner_field(self):
        """AppAPIKeyCreate should not have owner field — it is set server-side."""
        assert "owner" not in AppAPIKeyCreate.model_fields

    def test_no_policy_field(self):
        """AppAPIKeyCreate should not have policy field — it is set server-side with default."""
        assert "policy" not in AppAPIKeyCreate.model_fields


class TestAPIKeyQuery:
    """Test APIKeyQuery model."""

    def test_defaults(self):
        q = APIKeyQuery()
        assert q.app_id is None
        assert q.app_type is None
        assert q.status is None
        assert q.creator is None
        assert q.owner is None

    def test_with_filters(self):
        q = APIKeyQuery(app_id="app-1", status=APIKeyStatus.ACTIVE)
        assert q.app_id == "app-1"
        assert q.status == APIKeyStatus.ACTIVE

    def test_with_app_type(self):
        q = APIKeyQuery(app_type="bot")
        assert q.app_type == "bot"

    def test_with_owner(self):
        q = APIKeyQuery(owner="user-001")
        assert q.owner == "user-001"

    def test_with_all_filters(self):
        q = APIKeyQuery(
            app_id="app-1",
            app_type="bot",
            status=APIKeyStatus.ACTIVE,
            creator="admin",
            owner="user-001",
            tenant="t1",
        )
        assert q.app_id == "app-1"
        assert q.app_type == "bot"
        assert q.status == APIKeyStatus.ACTIVE
        assert q.creator == "admin"
        assert q.owner == "user-001"
        assert q.tenant == "t1"


_dt = datetime(2025, 1, 1, tzinfo=UTC)
_BASE_RESPONSE_ARGS = {
    "id": 1,
    "app_id": "app-1",
    "app_type": "web",
    "key_name": "k",
    "api_key_prefix": "sk-",
    "description": "d",
    "rate_limit_rpm": None,
    "rate_limit_rpd": None,
    "status": "ACTIVE",
    "owner": "u",
    "tenant": "default",
    "env": "dev",
    "creator": "u",
    "modifier": None,
    "policy": None,
    "gmt_create": _dt,
    "gmt_modified": _dt,
}


class TestAPIKeyResponse:
    """Test APIKeyResponse model."""

    def test_from_attributes(self):
        model = APIKeyResponse.model_validate(_BASE_RESPONSE_ARGS)
        assert model.id == 1
        assert model.api_key_prefix == "sk-"

    def test_has_no_api_key(self):
        """APIKeyResponse should NOT have api_key field."""
        model = APIKeyResponse.model_validate(_BASE_RESPONSE_ARGS)
        assert not hasattr(model, "api_key")


class TestAPIKeyCreateResponse:
    """Test APIKeyCreateResponse model."""

    def test_extends_response(self):
        model = APIKeyCreateResponse.model_validate(
            {**_BASE_RESPONSE_ARGS, "api_key": "sk-raw-secret"}
        )
        assert model.api_key == "sk-raw-secret"
        assert model.id == 1

    def test_api_key_required(self):
        with pytest.raises(ValidationError):
            APIKeyCreateResponse.model_validate(_BASE_RESPONSE_ARGS)


class TestAPIKeyListResponse:
    """Test APIKeyListResponse model."""

    def test_defaults(self):
        resp = APIKeyListResponse(items=[], total=0, page=1, page_size=20)
        assert resp.items == []
        assert resp.total == 0

    def test_with_items(self):
        items = [APIKeyResponse.model_validate(_BASE_RESPONSE_ARGS)]
        resp = APIKeyListResponse(items=items, total=1, page=1, page_size=20)
        assert len(resp.items) == 1
