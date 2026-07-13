"""Tests for api_gateway_router — user-facing API Key management endpoints.

Covers all route handlers:
  - create_bot_key (POST /bot)
  - create_app_key (POST /app)
  - list_my_keys (GET /)
  - get_key (GET /{prefix})
  - update_key (PUT /{prefix})
  - update_key_status (PATCH /{prefix}/status)
  - get_allowed_bots (GET /{prefix}/allowed-bots)
  - grant_allowed_bot (POST /{prefix}/allowed-bots/grant)
  - revoke_allowed_bot (POST /{prefix}/allowed-bots/revoke)

Plus internal helpers:
  - _check_api_key_permission
  - _get_api_key_or_404
  - _validate_allowed_bots_context
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from secbaas.community.adapters.web.routers.config_management.api_gateway_router import (
    BotIdRequest,
    StatusActionRequest,
    UpdateAPIKeyRequest,
    _check_api_key_permission,
    _get_api_key_or_404,
    _validate_allowed_bots_context,
    create_app_key,
    create_bot_key,
    get_allowed_bots,
    get_key,
    grant_allowed_bot,
    list_my_keys,
    revoke_allowed_bot,
    update_key,
    update_key_status,
)
from secbaas.community.api import ApiResponse, OperationContext
from secbaas.community.api.api_gateway import (
    APIKeyCreateResponse,
    APIKeyError,
    APIKeyListResponse,
    APIKeyResponse,
    APIKeyStatus,
)

# ─────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────


@pytest.fixture
def op_ctx() -> OperationContext:
    """Standard operation context."""
    return OperationContext(operator="user-001", env="test")


@pytest.fixture
def mock_service() -> AsyncMock:
    """Mock APIKeyService with all async methods."""
    return AsyncMock()


@pytest.fixture
def sample_key_response() -> APIKeyResponse:
    """Create a sample bot-type APIKeyResponse for testing."""
    return APIKeyResponse(
        id=42,
        app_id="bot-1:entity-789",
        app_type="bot",
        key_name="test-key",
        api_key_prefix="sk-abc",
        description="a test key",
        rate_limit_rpm=100,
        rate_limit_rpd=10000,
        status="ACTIVE",
        owner="user-001",
        tenant="tenant-1",
        env="test",
        creator="user-001",
        modifier=None,
        policy=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


@pytest.fixture
def sample_app_key_response() -> APIKeyResponse:
    """Create a sample app-type APIKeyResponse (with policy)."""
    return APIKeyResponse(
        id=43,
        app_id="my-app",
        app_type="app",
        key_name="app-key",
        api_key_prefix="sk-app",
        description="an app key",
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="user-001",
        tenant="tenant-1",
        env="test",
        creator="user-001",
        modifier=None,
        policy='{"allowed_bots": ["bot-1:entity-789"]}',
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


@pytest.fixture
def sample_empty_policy_key() -> APIKeyResponse:
    """App-type APIKeyResponse with empty allowed_bots."""
    return APIKeyResponse(
        id=44,
        app_id="my-app-2",
        app_type="app",
        key_name="empty-key",
        api_key_prefix="sk-empty",
        description="empty policy key",
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="user-001",
        tenant="tenant-1",
        env="test",
        creator="user-001",
        modifier=None,
        policy='{"allowed_bots": []}',
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


# ═════════════════════════════════════════════
# _check_api_key_permission
# ═════════════════════════════════════════════


class TestCheckApiKeyPermission:
    """Tests for the _check_api_key_permission dependency."""

    @pytest.mark.asyncio
    async def test_owner_passes(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Key owner should pass permission check."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        result = await _check_api_key_permission(
            "sk-abc", op_ctx=op_ctx, service=mock_service
        )
        assert result == sample_key_response

    @pytest.mark.asyncio
    async def test_key_not_found_raises_404(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Non-existent key prefix → 404."""
        mock_service.get_key_by_prefix.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await _check_api_key_permission(
                "sk-xxx", op_ctx=op_ctx, service=mock_service
            )
        assert exc_info.value.status_code == 404
        assert "不存在" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_no_permission_raises_403(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Non-owner, non-bot-entity → 403."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        # user-001 is owner, so use a different operator
        other_ctx = OperationContext(operator="other-user", env="test")
        with pytest.raises(HTTPException) as exc_info:
            await _check_api_key_permission(
                "sk-abc", op_ctx=other_ctx, service=mock_service
            )
        assert exc_info.value.status_code == 403
        assert "无权限" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_bot_entity_passes(
        self,
        mock_service: AsyncMock,
    ) -> None:
        """Bot entity (user == app_id entity) should pass."""
        bot_ctx = OperationContext(operator="entity-789", env="test")
        bot_key = APIKeyResponse(
            id=50,
            app_id="bot-1:entity-789",
            app_type="bot",
            key_name=None,
            api_key_prefix="sk-bot",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="different-owner",
            tenant="tenant-1",
            env="test",
            creator="different-owner",
            modifier=None,
            policy=None,
            gmt_create=datetime(2024, 1, 1),
            gmt_modified=datetime(2024, 1, 1),
        )
        mock_service.get_key_by_prefix.return_value = bot_key
        result = await _check_api_key_permission(
            "sk-bot", op_ctx=bot_ctx, service=mock_service
        )
        assert result == bot_key


# ═════════════════════════════════════════════
# _get_api_key_or_404
# ═════════════════════════════════════════════


class TestGetApiKeyOr404:
    """Tests for the _get_api_key_or_404 dependency."""

    @pytest.mark.asyncio
    async def test_key_found(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Existing key → return key (no permission check)."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        result = await _get_api_key_or_404(
            "sk-abc", op_ctx=op_ctx, service=mock_service
        )
        assert result == sample_key_response

    @pytest.mark.asyncio
    async def test_key_not_found_raises_404(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Non-existent prefix → 404."""
        mock_service.get_key_by_prefix.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await _get_api_key_or_404("sk-xxx", op_ctx=op_ctx, service=mock_service)
        assert exc_info.value.status_code == 404
        assert "不存在" in str(exc_info.value.detail)


# ═════════════════════════════════════════════
# _validate_allowed_bots_context
# ═════════════════════════════════════════════


class TestValidateAllowedBotsContext:
    """Tests for the _validate_allowed_bots_context helper."""

    @pytest.mark.asyncio
    async def test_non_app_key_raises_400(
        self,
        op_ctx: OperationContext,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Non-app-type key → 400."""
        with pytest.raises(HTTPException) as exc_info:
            await _validate_allowed_bots_context(
                "bot-1:entity-789", sample_key_response, op_ctx
            )
        assert exc_info.value.status_code == 400
        assert "仅 app_type=app" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_invalid_bot_id_raises_400(
        self,
        op_ctx: OperationContext,
        sample_app_key_response: APIKeyResponse,
    ) -> None:
        """Invalid bot_id format → 400."""
        with pytest.raises(HTTPException) as exc_info:
            await _validate_allowed_bots_context(
                "invalid-bot-id", sample_app_key_response, op_ctx
            )
        assert exc_info.value.status_code == 400
        assert "格式无效" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_no_permission_raises_403(
        self,
        sample_app_key_response: APIKeyResponse,
    ) -> None:
        """Bot owner mismatch → 403."""
        other_ctx = OperationContext(operator="wrong-user", env="test")
        with pytest.raises(HTTPException) as exc_info:
            await _validate_allowed_bots_context(
                "bot-1:entity-789", sample_app_key_response, other_ctx
            )
        assert exc_info.value.status_code == 403
        assert "无权限" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_owner_passes(
        self,
        sample_app_key_response: APIKeyResponse,
    ) -> None:
        """Same owner → passes."""
        op_ctx = OperationContext(operator="entity-789", env="test")
        result = await _validate_allowed_bots_context(
            "bot-1:entity-789", sample_app_key_response, op_ctx
        )
        assert result is None


# ═════════════════════════════════════════════
# create_bot_key (POST /bot)
# ═════════════════════════════════════════════


class TestCreateBotKey:
    """Tests for POST /bot endpoint."""

    @pytest.fixture
    def bot_ctx(self) -> OperationContext:
        """Operation context matching the entity_id in app_id."""
        return OperationContext(operator="entity-789", env="test")

    @pytest.mark.asyncio
    async def test_create_bot_key_success(
        self,
        bot_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Valid bot key creation should succeed."""
        from secbaas.community.api.api_gateway import BotAPIKeyCreate

        data = BotAPIKeyCreate(app_id="bot-1:entity-789", key_name="my-bot-key")
        mock_service.create_key.return_value = APIKeyCreateResponse(
            id=1,
            app_id="bot-1:entity-789",
            app_type="bot",
            key_name="my-bot-key",
            api_key_prefix="sk-abc",
            api_key="sk-abc-secret",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="user-001",
            tenant="team_claw",
            env="test",
            creator="user-001",
            modifier=None,
            policy=None,
            gmt_create=datetime(2024, 1, 1),
            gmt_modified=datetime(2024, 1, 1),
        )

        result = await create_bot_key(data, op_ctx=bot_ctx, service=mock_service)
        assert isinstance(result, ApiResponse)
        assert result.data.api_key_prefix == "sk-abc"
        mock_service.create_key.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_app_id_format(
        self,
        bot_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Invalid app_id (no colon) → 400."""
        from secbaas.community.api.api_gateway import BotAPIKeyCreate

        data = BotAPIKeyCreate(app_id="invalid-app-id", key_name="my-key")
        with pytest.raises(HTTPException) as exc_info:
            await create_bot_key(data, op_ctx=bot_ctx, service=mock_service)
        assert exc_info.value.status_code == 400
        assert "格式无效" in str(exc_info.value.detail)
        mock_service.create_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_permission(
        self,
        mock_service: AsyncMock,
    ) -> None:
        """Operator does not match entity_id → 403."""
        from secbaas.community.api.api_gateway import BotAPIKeyCreate

        other_ctx = OperationContext(operator="other-user", env="test")
        data = BotAPIKeyCreate(app_id="bot-1:entity-789", key_name="my-key")
        with pytest.raises(HTTPException) as exc_info:
            await create_bot_key(data, op_ctx=other_ctx, service=mock_service)
        assert exc_info.value.status_code == 403
        assert "无权限" in str(exc_info.value.detail)
        mock_service.create_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_key_error(
        self,
        bot_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """APIKeyError from service → 400."""
        from secbaas.community.api.api_gateway import BotAPIKeyCreate

        data = BotAPIKeyCreate(app_id="bot-1:entity-789", key_name="my-key")
        mock_service.create_key.side_effect = APIKeyError(
            code=1001, message="duplicate key"
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_bot_key(data, op_ctx=bot_ctx, service=mock_service)
        assert exc_info.value.status_code == 400
        assert "duplicate key" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_unexpected_error(
        self,
        bot_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Unexpected service exception → 500."""
        from secbaas.community.api.api_gateway import BotAPIKeyCreate

        data = BotAPIKeyCreate(app_id="bot-1:entity-789", key_name="my-key")
        mock_service.create_key.side_effect = RuntimeError("db down")
        with pytest.raises(HTTPException) as exc_info:
            await create_bot_key(data, op_ctx=bot_ctx, service=mock_service)
        assert exc_info.value.status_code == 500
        assert "创建 API Key 失败" in str(exc_info.value.detail)
        assert "db down" not in str(exc_info.value.detail)


# ═════════════════════════════════════════════
# create_app_key (POST /app)
# ═════════════════════════════════════════════


class TestCreateAppKey:
    """Tests for POST /app endpoint."""

    @pytest.mark.asyncio
    async def test_create_app_key_success(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Valid app key creation should succeed."""
        from secbaas.community.api.api_gateway import AppAPIKeyCreate

        data = AppAPIKeyCreate(app_id="my-app", key_name="app-key")
        mock_service.create_key.return_value = APIKeyCreateResponse(
            id=2,
            app_id="my-app",
            app_type="app",
            key_name="app-key",
            api_key_prefix="sk-app",
            api_key="sk-app-secret",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="user-001",
            tenant="team_claw",
            env="test",
            creator="user-001",
            modifier=None,
            policy='{"allowed_bots": []}',
            gmt_create=datetime(2024, 1, 1),
            gmt_modified=datetime(2024, 1, 1),
        )

        result = await create_app_key(data, op_ctx=op_ctx, service=mock_service)
        assert isinstance(result, ApiResponse)
        assert result.data.api_key_prefix == "sk-app"
        mock_service.create_key.assert_called_once()
        # Verify owner and policy are auto-set
        call_args = mock_service.create_key.call_args
        create_dto = call_args[0][0]
        assert create_dto.owner == "user-001"
        assert create_dto.app_type == "app"

    @pytest.mark.asyncio
    async def test_create_app_key_default_policy_is_deny_all(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """create_app_key sets default policy to '{\"allowed_bots\":[]}' (deny all)."""
        from secbaas.community.api.api_gateway import AppAPIKeyCreate

        data = AppAPIKeyCreate(app_id="my-app")
        mock_service.create_key.return_value = APIKeyCreateResponse(
            id=2,
            app_id="my-app",
            app_type="app",
            key_name=None,
            api_key_prefix="sk-app",
            api_key="sk-app-secret",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="user-001",
            tenant="team_claw",
            env="test",
            creator="user-001",
            modifier=None,
            policy='{"allowed_bots":[]}',
            gmt_create=datetime(2024, 1, 1),
            gmt_modified=datetime(2024, 1, 1),
        )

        await create_app_key(data, op_ctx=op_ctx, service=mock_service)
        call_args = mock_service.create_key.call_args
        create_dto = call_args[0][0]
        assert create_dto.policy == '{"allowed_bots":[]}'

    @pytest.mark.asyncio
    async def test_api_key_error(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """APIKeyError from service → 400."""
        from secbaas.community.api.api_gateway import AppAPIKeyCreate

        data = AppAPIKeyCreate(app_id="my-app")
        mock_service.create_key.side_effect = APIKeyError(
            code=1002, message="limit exceeded"
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_app_key(data, op_ctx=op_ctx, service=mock_service)
        assert exc_info.value.status_code == 400
        assert "limit exceeded" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_unexpected_error(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Unexpected service exception → 500."""
        from secbaas.community.api.api_gateway import AppAPIKeyCreate

        data = AppAPIKeyCreate(app_id="my-app")
        mock_service.create_key.side_effect = ValueError("db error")
        with pytest.raises(HTTPException) as exc_info:
            await create_app_key(data, op_ctx=op_ctx, service=mock_service)
        assert exc_info.value.status_code == 500
        assert "创建 API Key 失败" in str(exc_info.value.detail)
        assert "db error" not in str(exc_info.value.detail)


# ═════════════════════════════════════════════
# list_my_keys (GET /)
# ═════════════════════════════════════════════


class TestListMyKeys:
    """Tests for GET / endpoint."""

    @pytest.mark.asyncio
    async def test_list_keys_success(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """List keys should return keys owned by the current user."""
        list_response = APIKeyListResponse(
            items=[sample_key_response], total=1, page=1, page_size=20
        )
        mock_service.list_keys.return_value = list_response

        result = await list_my_keys(
            app_type=None,
            status=None,
            page=1,
            page_size=20,
            op_ctx=op_ctx,
            service=mock_service,
        )
        assert isinstance(result, ApiResponse)
        assert result.data == list_response
        mock_service.list_keys.assert_called_once()
        # Verify the query filters by current user
        call_args = mock_service.list_keys.call_args
        query = call_args.kwargs["query"]
        assert query.owner == "user-001"

    @pytest.mark.asyncio
    async def test_list_keys_with_filters(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """List keys with app_type and status filters."""
        mock_service.list_keys.return_value = APIKeyListResponse(
            items=[], total=0, page=1, page_size=10
        )
        result = await list_my_keys(
            app_type="bot",
            status=APIKeyStatus.ACTIVE,
            page=2,
            page_size=10,
            op_ctx=op_ctx,
            service=mock_service,
        )
        assert isinstance(result, ApiResponse)
        call_args = mock_service.list_keys.call_args
        query = call_args.kwargs["query"]
        assert query.app_type == "bot"
        assert query.status == APIKeyStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_list_keys_empty(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Empty result should return normally."""
        mock_service.list_keys.return_value = APIKeyListResponse(
            items=[], total=0, page=1, page_size=20
        )
        result = await list_my_keys(
            app_type=None,
            status=None,
            page=1,
            page_size=20,
            op_ctx=op_ctx,
            service=mock_service,
        )
        assert isinstance(result, ApiResponse)
        assert len(result.data.items) == 0
        assert result.data.total == 0


# ═════════════════════════════════════════════
# get_key (GET /{api_key_prefix})
# ═════════════════════════════════════════════


class TestGetKey:
    """Tests for GET /{api_key_prefix} endpoint."""

    @pytest.mark.asyncio
    async def test_get_key_success(
        self,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Get key by prefix should return the key."""
        result = await get_key("sk-abc", sample_key_response)
        assert isinstance(result, ApiResponse)
        assert result.data == sample_key_response


# ═════════════════════════════════════════════
# update_key (PUT /{api_key_prefix})
# ═════════════════════════════════════════════


class TestUpdateKey:
    """Tests for PUT /{api_key_prefix} endpoint."""

    @pytest.mark.asyncio
    async def test_update_key_success(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Update key metadata should succeed."""
        data = UpdateAPIKeyRequest(key_name="new-name", description="updated desc")
        mock_service.update_key_by_prefix.return_value = sample_key_response

        result = await update_key(
            "sk-abc", data, sample_key_response, op_ctx, service=mock_service
        )
        assert isinstance(result, ApiResponse)
        assert result.data == sample_key_response
        mock_service.update_key_by_prefix.assert_called_once()
        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        assert update_dto.key_name == "new-name"
        assert update_dto.description == "updated desc"


# ═════════════════════════════════════════════
# update_key_status (PATCH /{api_key_prefix}/status)
# ═════════════════════════════════════════════


class TestUpdateKeyStatus:
    """Tests for PATCH /{api_key_prefix}/status endpoint."""

    @pytest.mark.asyncio
    async def test_activate_success(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Activate action should call activate_by_prefix."""
        mock_service.activate_by_prefix.return_value = sample_key_response
        data = StatusActionRequest(action="activate")

        result = await update_key_status(
            "sk-abc", data, sample_key_response, op_ctx, service=mock_service
        )
        assert isinstance(result, ApiResponse)
        assert result.message == "API Key 已启用"
        mock_service.activate_by_prefix.assert_called_once_with("sk-abc", op_ctx)

    @pytest.mark.asyncio
    async def test_deactivate_success(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Deactivate action should call deactivate_by_prefix."""
        mock_service.deactivate_by_prefix.return_value = sample_key_response
        data = StatusActionRequest(action="deactivate")

        result = await update_key_status(
            "sk-abc", data, sample_key_response, op_ctx, service=mock_service
        )
        assert isinstance(result, ApiResponse)
        assert result.message == "API Key 已停用"
        mock_service.deactivate_by_prefix.assert_called_once_with("sk-abc", op_ctx)

    @pytest.mark.asyncio
    async def test_revoke_success(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Revoke action should call revoke_by_prefix."""
        mock_service.revoke_by_prefix.return_value = sample_key_response
        data = StatusActionRequest(action="revoke")

        result = await update_key_status(
            "sk-abc", data, sample_key_response, op_ctx, service=mock_service
        )
        assert isinstance(result, ApiResponse)
        assert result.message == "API Key 已吊销"
        mock_service.revoke_by_prefix.assert_called_once_with("sk-abc", op_ctx)

    @pytest.mark.asyncio
    async def test_key_not_found_returns_404(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Service returns None → 404."""
        mock_service.activate_by_prefix.return_value = None
        data = StatusActionRequest(action="activate")

        with pytest.raises(HTTPException) as exc_info:
            await update_key_status(
                "sk-abc", data, sample_key_response, op_ctx, service=mock_service
            )
        assert exc_info.value.status_code == 404
        assert "不存在" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_api_key_error_returns_400(
        self,
        op_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """APIKeyError from service → 400."""
        mock_service.activate_by_prefix.side_effect = APIKeyError(
            code=1001, message="Key is already ACTIVE"
        )
        data = StatusActionRequest(action="activate")

        with pytest.raises(HTTPException) as exc_info:
            await update_key_status(
                "sk-abc", data, sample_key_response, op_ctx, service=mock_service
            )
        assert exc_info.value.status_code == 400
        assert "Key is already ACTIVE" in str(exc_info.value.detail)


# ═════════════════════════════════════════════
# get_allowed_bots (GET /{api_key_prefix}/allowed-bots)
# ═════════════════════════════════════════════


class TestGetAllowedBots:
    """Tests for GET /{api_key_prefix}/allowed-bots endpoint."""

    @pytest.mark.asyncio
    async def test_get_allowed_bots_success(
        self,
        sample_app_key_response: APIKeyResponse,
    ) -> None:
        """App-type key → return allowed_bots list."""
        result = await get_allowed_bots("sk-app", sample_app_key_response)
        assert isinstance(result, ApiResponse)
        assert result.data == {"allowed_bots": ["bot-1:entity-789"]}

    @pytest.mark.asyncio
    async def test_non_app_key_raises_400(
        self,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Non-app key → 400."""
        with pytest.raises(HTTPException) as exc_info:
            await get_allowed_bots("sk-abc", sample_key_response)
        assert exc_info.value.status_code == 400
        assert "仅 app_type=app" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_empty_policy(
        self,
        sample_empty_policy_key: APIKeyResponse,
    ) -> None:
        """Key with empty allowed_bots → return empty list."""
        result = await get_allowed_bots("sk-empty", sample_empty_policy_key)
        assert isinstance(result, ApiResponse)
        assert result.data == {"allowed_bots": []}

    @pytest.mark.asyncio
    async def test_none_policy(
        self,
        sample_app_key_response: APIKeyResponse,
    ) -> None:
        """Key with None policy → legacy allow-all, normalized to ['*']."""
        key = sample_app_key_response.model_copy(update={"policy": None})
        result = await get_allowed_bots("sk-app", key)
        assert isinstance(result, ApiResponse)
        assert result.data == {"allowed_bots": ["*"]}

    @pytest.mark.asyncio
    async def test_none_sentinel_filtered_out(
        self,
    ) -> None:
        """NONE sentinel value should be filtered from allowed_bots result."""
        key = APIKeyResponse(
            id=50,
            app_id="my-app",
            app_type="app",
            key_name="none-key",
            api_key_prefix="sk-none",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="user-001",
            tenant="tenant-1",
            env="test",
            creator="user-001",
            modifier=None,
            policy='{"allowed_bots":["NONE"]}',
            gmt_create=datetime(2024, 1, 1),
            gmt_modified=datetime(2024, 1, 1),
        )
        result = await get_allowed_bots("sk-none", key)
        assert isinstance(result, ApiResponse)
        assert result.data == {"allowed_bots": []}

    @pytest.mark.asyncio
    async def test_mixed_none_and_real_bots(
        self,
    ) -> None:
        """NONE is filtered but real bot IDs remain."""
        key = APIKeyResponse(
            id=51,
            app_id="my-app",
            app_type="app",
            key_name="mixed-key",
            api_key_prefix="sk-mixed",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="user-001",
            tenant="tenant-1",
            env="test",
            creator="user-001",
            modifier=None,
            policy='{"allowed_bots":["NONE","bot-1:entity-789"]}',
            gmt_create=datetime(2024, 1, 1),
            gmt_modified=datetime(2024, 1, 1),
        )
        result = await get_allowed_bots("sk-mixed", key)
        assert isinstance(result, ApiResponse)
        assert result.data == {"allowed_bots": ["bot-1:entity-789"]}


# ═════════════════════════════════════════════
# grant_allowed_bot (POST /{api_key_prefix}/allowed-bots/grant)
# ═════════════════════════════════════════════


class TestGrantAllowedBot:
    """Tests for POST /{api_key_prefix}/allowed-bots/grant endpoint."""

    @pytest.mark.asyncio
    async def test_grant_new_bot_success(
        self,
        mock_service: AsyncMock,
        sample_empty_policy_key: APIKeyResponse,
    ) -> None:
        """Grant a new bot → append to allowed_bots."""
        op_ctx = OperationContext(operator="entity-001", env="test")
        data = BotIdRequest(bot_id="new-bot:entity-001")
        updated_key = sample_empty_policy_key.model_copy(
            update={"policy": '{"allowed_bots":["new-bot:entity-001"]}'}
        )
        mock_service.update_key_by_prefix.return_value = updated_key

        result = await grant_allowed_bot(
            "sk-empty", data, sample_empty_policy_key, op_ctx, service=mock_service
        )
        assert isinstance(result, ApiResponse)
        assert result.message == "allowed_bots 已授权"
        mock_service.update_key_by_prefix.assert_called_once()
        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        assert "new-bot:entity-001" in update_dto.policy

    @pytest.mark.asyncio
    async def test_grant_replaces_none_sentinel(
        self,
        mock_service: AsyncMock,
    ) -> None:
        """When policy is ['NONE'], grant normalizes it to empty and appends the bot_id."""
        none_policy_key = APIKeyResponse(
            id=60,
            app_id="my-app",
            app_type="app",
            key_name="none-policy-key",
            api_key_prefix="sk-none-pol",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="user-001",
            tenant="tenant-1",
            env="test",
            creator="user-001",
            modifier=None,
            policy='{"allowed_bots":["NONE"]}',
            gmt_create=datetime(2024, 1, 1),
            gmt_modified=datetime(2024, 1, 1),
        )
        op_ctx = OperationContext(operator="entity-001", env="test")
        data = BotIdRequest(bot_id="new-bot:entity-001")
        updated_key = none_policy_key.model_copy(
            update={"policy": '{"allowed_bots":["new-bot:entity-001"]}'}
        )
        mock_service.update_key_by_prefix.return_value = updated_key

        result = await grant_allowed_bot(
            "sk-none-pol", data, none_policy_key, op_ctx, service=mock_service
        )
        assert isinstance(result, ApiResponse)
        assert result.message == "allowed_bots 已授权"
        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        # NONE should be gone, only the new bot_id remains
        import json

        parsed_policy = json.loads(update_dto.policy)
        assert "NONE" not in parsed_policy["allowed_bots"]
        assert "new-bot:entity-001" in parsed_policy["allowed_bots"]

    @pytest.mark.asyncio
    async def test_grant_existing_bot_idempotent(
        self,
        mock_service: AsyncMock,
        sample_app_key_response: APIKeyResponse,
    ) -> None:
        """Grant a bot that is already in the list → still succeed (no duplicate)."""
        op_ctx = OperationContext(operator="entity-789", env="test")
        data = BotIdRequest(bot_id="bot-1:entity-789")
        mock_service.update_key_by_prefix.return_value = sample_app_key_response

        result = await grant_allowed_bot(
            "sk-app", data, sample_app_key_response, op_ctx, service=mock_service
        )
        assert isinstance(result, ApiResponse)
        assert result.message == "allowed_bots 已授权"
        # Verify policy still only has one entry
        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        assert update_dto.policy.count("bot-1:entity-789") == 1

    @pytest.mark.asyncio
    async def test_update_returns_none_raises_404(
        self,
        mock_service: AsyncMock,
        sample_empty_policy_key: APIKeyResponse,
    ) -> None:
        """Service returns None after update → 404."""
        op_ctx = OperationContext(operator="entity-001", env="test")
        data = BotIdRequest(bot_id="new-bot:entity-001")
        mock_service.update_key_by_prefix.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await grant_allowed_bot(
                "sk-empty", data, sample_empty_policy_key, op_ctx, service=mock_service
            )
        assert exc_info.value.status_code == 404
        assert "不存在" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_validate_context_called(
        self,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Non-app key passed to grant → validation raises 400."""
        op_ctx = OperationContext(operator="entity-789", env="test")
        data = BotIdRequest(bot_id="bot-1:entity-789")

        with pytest.raises(HTTPException) as exc_info:
            await grant_allowed_bot(
                "sk-abc", data, sample_key_response, op_ctx, service=mock_service
            )
        assert exc_info.value.status_code == 400
        mock_service.update_key_by_prefix.assert_not_called()


# ═════════════════════════════════════════════
# revoke_allowed_bot (POST /{api_key_prefix}/allowed-bots/revoke)
# ═════════════════════════════════════════════


class TestRevokeAllowedBot:
    """Tests for POST /{api_key_prefix}/allowed-bots/revoke endpoint."""

    @pytest.mark.asyncio
    async def test_revoke_existing_bot_success(
        self,
        mock_service: AsyncMock,
        sample_app_key_response: APIKeyResponse,
    ) -> None:
        """Revoke an existing bot → remove from allowed_bots."""
        op_ctx = OperationContext(operator="entity-789", env="test")
        data = BotIdRequest(bot_id="bot-1:entity-789")
        updated_key = sample_app_key_response.model_copy(
            update={"policy": '{"allowed_bots":[]}'}
        )
        mock_service.update_key_by_prefix.return_value = updated_key

        result = await revoke_allowed_bot(
            "sk-app", data, sample_app_key_response, op_ctx, service=mock_service
        )
        assert isinstance(result, ApiResponse)
        assert result.message == "allowed_bots 已撤销"
        mock_service.update_key_by_prefix.assert_called_once()

    @pytest.mark.asyncio
    async def test_revoke_non_existent_bot_raises_404(
        self,
        mock_service: AsyncMock,
        sample_app_key_response: APIKeyResponse,
    ) -> None:
        """Revoke a bot that is not in the list → 404."""
        op_ctx = OperationContext(operator="entity-789", env="test")
        data = BotIdRequest(bot_id="unknown-bot:entity-789")

        with pytest.raises(HTTPException) as exc_info:
            await revoke_allowed_bot(
                "sk-app", data, sample_app_key_response, op_ctx, service=mock_service
            )
        assert exc_info.value.status_code == 404
        assert "不在 allowed_bots 中" in str(exc_info.value.detail)
        mock_service.update_key_by_prefix.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_returns_none_raises_404(
        self,
        mock_service: AsyncMock,
        sample_app_key_response: APIKeyResponse,
    ) -> None:
        """Service returns None after update → 404."""
        op_ctx = OperationContext(operator="entity-789", env="test")
        data = BotIdRequest(bot_id="bot-1:entity-789")
        mock_service.update_key_by_prefix.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await revoke_allowed_bot(
                "sk-app", data, sample_app_key_response, op_ctx, service=mock_service
            )
        assert exc_info.value.status_code == 404
        assert "不存在" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_validate_context_called(
        self,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Non-app key passed to revoke → validation raises 400."""
        op_ctx = OperationContext(operator="entity-789", env="test")
        data = BotIdRequest(bot_id="bot-1:entity-789")

        with pytest.raises(HTTPException) as exc_info:
            await revoke_allowed_bot(
                "sk-abc", data, sample_key_response, op_ctx, service=mock_service
            )
        assert exc_info.value.status_code == 400
        mock_service.update_key_by_prefix.assert_not_called()
