"""Unit tests for api/domain/common.py — shared API domain types."""

import pytest
from pydantic import ValidationError

from secbaas.community.api import (
    ApiResponse,
    BaseRequest,
    DomainError,
    ListResponse,
    OperationContext,
    RequestContext,
    RequestId,
    SuccessResponse,
    WithRequestId,
    validate_request_id,
)

# ==================== RequestId Validation ====================


class TestValidateRequestId:
    """Tests for the validate_request_id validator function."""

    def test_valid_32_char(self):
        """WHEN request_id is exactly 32 alphanumeric chars, THEN passes."""
        assert validate_request_id("a" * 32) == "a" * 32

    def test_valid_64_char(self):
        """WHEN request_id is exactly 64 chars with hyphens, THEN passes."""
        rid = f"{'a' * 30}-{'b' * 33}"
        assert len(rid) == 64
        assert validate_request_id(rid) == rid

    def test_valid_with_underscores(self):
        """WHEN request_id contains underscores, THEN passes."""
        assert validate_request_id("a" * 30 + "_" + "b" * 30) is not None

    def test_too_short(self):
        """WHEN request_id is under 32 chars, THEN raises ValueError."""
        with pytest.raises(ValueError, match="at least 32 characters"):
            validate_request_id("short")

    def test_too_long(self):
        """WHEN request_id exceeds 64 chars, THEN raises ValueError."""
        with pytest.raises(ValueError, match="at most 64 characters"):
            validate_request_id("a" * 65)

    def test_invalid_characters(self):
        """WHEN request_id contains special chars, THEN raises ValueError."""
        with pytest.raises(ValueError, match="alphanumeric"):
            validate_request_id("a" * 31 + "!" + "b" * 31)


class TestRequestIdType:
    """Tests for the RequestId annotated type via WithRequestId."""

    def test_valid_request_id_accepted(self):
        """WHEN valid request_id provided, THEN model validates."""
        model = WithRequestId(request_id="a" * 32)
        assert len(model.request_id) == 32

    def test_invalid_request_id_rejected(self):
        """WHEN invalid request_id provided, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            WithRequestId(request_id="short")

    def test_request_id_is_annotated_str(self):
        """THEN RequestId is an Annotated type, not a custom class."""
        origin = getattr(RequestId, "__origin__", None)
        assert origin is not None, "RequestId should be an Annotated type"


# ==================== ApiResponse ====================


class TestApiResponse:
    """Tests for the generic ApiResponse model."""

    def test_default_success(self):
        """WHEN created with defaults, THEN code=0, message='success'."""
        resp = ApiResponse(data=None)
        assert resp.code == 0
        assert resp.message == "success"
        assert resp.data is None

    def test_with_data(self):
        """WHEN created with string data, THEN data is accessible."""
        resp = ApiResponse(data="hello")
        assert resp.data == "hello"

    def test_with_dict_data(self):
        """WHEN created with dict data, THEN generic type works."""
        resp = ApiResponse[dict](data={"key": "value"})
        assert resp.data["key"] == "value"

    def test_from_attributes(self):
        """THEN model_config has from_attributes=True for ORM compat."""
        assert ApiResponse.model_config.get("from_attributes") is True

    def test_custom_code_and_message(self):
        """WHEN custom code and message provided, THEN they are stored."""
        resp = ApiResponse(data=None, code=1001, message="custom error")
        assert resp.code == 1001
        assert resp.message == "custom error"


# ==================== ListResponse ====================


class TestListResponse:
    """Tests for the generic ListResponse model."""

    def test_default_empty(self):
        """WHEN created with defaults, THEN items is empty list, total=0."""
        resp = ListResponse()
        assert resp.items == []
        assert resp.total == 0
        assert resp.page == 1
        assert resp.page_size == 20

    def test_with_items(self):
        """WHEN created with items, THEN they are stored."""
        resp = ListResponse(items=["a", "b"], total=2)
        assert len(resp.items) == 2
        assert resp.total == 2

    def test_page_size_upper_bound(self):
        """WHEN page_size exceeds 100, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            ListResponse(page_size=101)

    def test_page_size_lower_bound(self):
        """WHEN page_size is 0, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            ListResponse(page_size=0)

    def test_page_ge_1(self):
        """WHEN page is 0, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            ListResponse(page=0)

    def test_from_attributes(self):
        """THEN model_config has from_attributes=True."""
        assert ListResponse.model_config.get("from_attributes") is True

    def test_typed_items(self):
        """WHEN typed with int, THEN items accept int values."""
        resp = ListResponse[int](items=[1, 2, 3], total=3)
        assert resp.items == [1, 2, 3]


# ==================== SuccessResponse ====================


class TestSuccessResponse:
    """Tests for SuccessResponse model."""

    def test_default_success(self):
        """WHEN created with defaults, THEN success=True."""
        resp = SuccessResponse()
        assert resp.success is True
        assert resp.message == "Operation completed successfully"

    def test_custom_message(self):
        """WHEN custom message provided, THEN it is stored."""
        resp = SuccessResponse(message="Tenant deleted")
        assert resp.message == "Tenant deleted"

    def test_failure(self):
        """WHEN success=False, THEN it is stored."""
        resp = SuccessResponse(success=False)
        assert resp.success is False

    def test_from_attributes(self):
        """THEN model_config has from_attributes=True."""
        assert SuccessResponse.model_config.get("from_attributes") is True


# ==================== BaseRequest ====================


class TestBaseRequest:
    """Tests for BaseRequest (alias of WithRequestId)."""

    def test_base_request_is_with_request_id(self):
        """THEN BaseRequest is the same class as WithRequestId."""
        assert BaseRequest is WithRequestId

    def test_valid_request(self):
        """WHEN valid request_id provided, THEN model validates."""
        req = BaseRequest(request_id="a" * 32)
        assert req.request_id == "a" * 32


# ==================== DomainError ====================


class TestDomainError:
    """Tests for the DomainError base exception."""

    def test_default_values(self):
        """THEN default error_code and http_status are set."""
        err = DomainError()
        assert err.error_code == "DOMAIN_ERROR"
        assert err.http_status == 500
        assert err.message == ""

    def test_with_message(self):
        """WHEN message provided, THEN it is stored."""
        err = DomainError("Something went wrong")
        assert err.message == "Something went wrong"
        assert str(err) == "Something went wrong"

    def test_custom_subclass(self):
        """WHEN subclassed, THEN custom codes work."""

        class NotFoundError(DomainError):
            error_code = "NOT_FOUND"
            http_status = 404

        err = NotFoundError("Item not found")
        assert err.error_code == "NOT_FOUND"
        assert err.http_status == 404
        assert err.message == "Item not found"


# ==================== RequestContext ====================


class TestRequestContext:
    """Tests for the RequestContext dataclass."""

    def test_required_user_id(self):
        """WHEN created with only user_id, THEN nick_name defaults to None."""
        ctx = RequestContext(user_id="user123")
        assert ctx.user_id == "user123"
        assert ctx.nick_name is None

    def test_with_nick_name(self):
        """WHEN nick_name provided, THEN it is stored."""
        ctx = RequestContext(user_id="user123", nick_name="花名")
        assert ctx.user_id == "user123"
        assert ctx.nick_name == "花名"

    def test_is_dataclass(self):
        """THEN RequestContext is a dataclass."""
        import dataclasses

        assert dataclasses.is_dataclass(RequestContext)

    def test_immutable_by_default(self):
        """THEN dataclass fields are mutable by default (no frozen)."""
        ctx = RequestContext(user_id="user123")
        ctx.user_id = "user456"
        assert ctx.user_id == "user456"

    def test_positional_args(self):
        """THEN positional arguments work."""
        ctx = RequestContext("user123", "nick")
        assert ctx.user_id == "user123"
        assert ctx.nick_name == "nick"

    def test_repr(self):
        """THEN repr is generated by dataclass."""
        ctx = RequestContext(user_id="u1", nick_name="n1")
        r = repr(ctx)
        assert "RequestContext" in r
        assert "user_id=" in r


# ==================== OperationContext ====================


class TestOperationContext:
    """Tests for the OperationContext Pydantic model."""

    def test_required_fields(self):
        """WHEN operator and env provided, THEN model validates."""
        ctx = OperationContext(operator="user123", env="dev")
        assert ctx.operator == "user123"
        assert ctx.env == "dev"

    def test_operator_too_short(self):
        """WHEN operator is empty, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            OperationContext(operator="", env="dev")

    def test_operator_too_long(self):
        """WHEN operator exceeds 128 chars, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            OperationContext(operator="x" * 129, env="dev")

    def test_env_too_short(self):
        """WHEN env is empty, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            OperationContext(operator="user", env="")

    def test_env_too_long(self):
        """WHEN env exceeds 128 chars, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            OperationContext(operator="user", env="x" * 129)
