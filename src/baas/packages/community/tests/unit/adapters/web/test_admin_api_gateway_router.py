"""Tests for admin_api_gateway_router (管理员接口).

Covers all route handlers:
  - list_all_keys
  - create_key_admin
  - update_api_key_config
  - get_key_admin
  - update_key_status_admin
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from secbaas.adapters.web.routers.admin.api_gateway_router import (
    AdminBotIdRequest,
    AdminCreateAPIKeyRequest,
    AdminStatusActionRequest,
    UpdateAPIKeyConfigRequest,
    _require_admin,
    create_key_admin,
    get_allowed_bots_admin,
    get_key_admin,
    grant_allowed_bot_admin,
    list_all_keys,
    revoke_allowed_bot_admin,
    update_api_key_config,
    update_key_status_admin,
)
from secbaas.api import ApiResponse, OperationContext
from secbaas.api.api_gateway import (
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
def admin_ctx() -> OperationContext:
    """Admin operation context (staff ID in ADMIN_OPERATORS)."""
    return OperationContext(operator="151614", env="test")


@pytest.fixture
def mock_service() -> AsyncMock:
    """Mock APIKeyService with all async methods."""
    svc = AsyncMock()
    return svc


@pytest.fixture
def sample_key_response() -> APIKeyResponse:
    """Create a sample APIKeyResponse for testing."""
    from datetime import datetime

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
def create_response() -> APIKeyCreateResponse:
    """Standard create response."""
    from datetime import datetime

    return APIKeyCreateResponse(
        id=1,
        app_id="app-1",
        app_type="app",
        key_name="admin-key",
        api_key_prefix="sk-admin",
        api_key="sk-admin-secret-full",
        description="admin created",
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="admin-001",
        tenant="tenant-1",
        env="test",
        creator="151614",
        modifier=None,
        policy=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


# ═════════════════════════════════════════════
# _require_admin
# ═════════════════════════════════════════════


class TestRequireAdmin:
    """Tests for the _require_admin dependency."""

    @pytest.mark.asyncio
    async def test_admin_passes(self, admin_ctx: OperationContext) -> None:
        """Admin should pass."""
        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await _require_admin(admin_ctx)
            assert result == admin_ctx

    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self, op_ctx: OperationContext) -> None:
        """Non-admin should raise 403."""
        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _require_admin(op_ctx)

        assert exc_info.value.status_code == 403
        assert "无权限" in str(exc_info.value.detail)


# ═════════════════════════════════════════════
# list_all_keys
# ═════════════════════════════════════════════


class TestListAllKeys:
    """Tests for GET /admin/api-keys endpoint."""

    @pytest.fixture
    def list_response(self) -> APIKeyListResponse:
        """Create a sample list response with two keys."""
        from datetime import datetime

        key1 = APIKeyResponse(
            id=1,
            app_id="app-1",
            app_type="bot",
            key_name="key-1",
            api_key_prefix="sk-aa",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="user-001",
            tenant=None,
            env="test",
            creator="user-001",
            modifier=None,
            policy=None,
            gmt_create=datetime(2024, 1, 1),
            gmt_modified=datetime(2024, 1, 1),
        )
        key2 = APIKeyResponse(
            id=2,
            app_id="app-2",
            app_type="bot",
            key_name="key-2",
            api_key_prefix="sk-bb",
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="INACTIVE",
            owner="user-002",
            tenant=None,
            env="test",
            creator="user-002",
            modifier=None,
            policy=None,
            gmt_create=datetime(2024, 2, 1),
            gmt_modified=datetime(2024, 2, 1),
        )
        return APIKeyListResponse(items=[key1, key2], total=2, page=1, page_size=20)

    @pytest.mark.asyncio
    async def test_list_all_keys_admin_success(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        list_response: APIKeyListResponse,
    ) -> None:
        """Admin should be able to list keys with default pagination."""
        mock_service.list_keys.return_value = list_response

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await list_all_keys(
                app_id=None,
                app_type=None,
                status=None,
                creator=None,
                owner=None,
                tenant=None,
                page=1,
                page_size=20,
                _op_ctx=admin_ctx,
                service=mock_service,
            )

        assert isinstance(result, ApiResponse)
        assert result.data == list_response
        mock_service.list_keys.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_all_keys_with_filters(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Admin can apply all filter parameters."""
        empty_list = APIKeyListResponse(items=[], total=0, page=1, page_size=10)
        mock_service.list_keys.return_value = empty_list

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await list_all_keys(
                app_id="app-1",
                app_type="bot",
                status=APIKeyStatus.ACTIVE,
                creator="user-001",
                owner="user-001",
                tenant="tenant-1",
                page=2,
                page_size=10,
                _op_ctx=admin_ctx,
                service=mock_service,
            )

        assert result.data == empty_list
        call_args = mock_service.list_keys.call_args
        query = call_args.kwargs["query"]
        assert query.app_id == "app-1"
        assert query.app_type == "bot"
        assert query.status == APIKeyStatus.ACTIVE
        assert query.creator == "user-001"
        assert query.owner == "user-001"
        assert query.tenant == "tenant-1"

    @pytest.mark.asyncio
    async def test_list_all_keys_empty_result(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Admin: empty list should return normally."""
        empty_list = APIKeyListResponse(items=[], total=0, page=1, page_size=20)
        mock_service.list_keys.return_value = empty_list

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await list_all_keys(
                app_id=None,
                app_type=None,
                status=None,
                creator=None,
                owner=None,
                tenant=None,
                page=1,
                page_size=20,
                _op_ctx=admin_ctx,
                service=mock_service,
            )

        assert result.data == empty_list
        assert len(result.data.items) == 0


# ═════════════════════════════════════════════
# create_key_admin
# ═════════════════════════════════════════════


class TestCreateKeyAdmin:
    """Tests for POST /admin/api-keys endpoint."""

    @pytest.mark.asyncio
    async def test_admin_create_key_success(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        create_response: APIKeyCreateResponse,
    ) -> None:
        """Admin should be able to create any type of key."""
        data = AdminCreateAPIKeyRequest(
            app_type="app",
            app_id="app-1",
            key_name="admin-key",
            description="admin created",
        )
        mock_service.create_key.return_value = create_response

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await create_key_admin(
                data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        assert result.data == create_response
        mock_service.create_key.assert_called_once()
        call_args = mock_service.create_key.call_args
        create_dto = call_args[0][0]
        assert create_dto.app_type == "app"
        assert create_dto.app_id == "app-1"
        assert create_dto.key_name == "admin-key"

    @pytest.mark.asyncio
    async def test_admin_create_key_with_all_fields(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        create_response: APIKeyCreateResponse,
    ) -> None:
        """Admin can create key with all fields."""
        data = AdminCreateAPIKeyRequest(
            app_type="bot",
            app_id="bot-1:entity-001",
            key_name="full-key",
            description="with all fields",
            rate_limit_rpm=50,
            rate_limit_rpd=5000,
            owner="user-002",
            tenant="tenant-xyz",
            policy='{"allow": ["read"]}',
        )
        mock_service.create_key.return_value = create_response

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await create_key_admin(
                data, _op_ctx=admin_ctx, service=mock_service
            )

        call_args = mock_service.create_key.call_args
        create_dto = call_args[0][0]
        assert create_dto.app_type == "bot"
        assert create_dto.app_id == "bot-1:entity-001"
        assert create_dto.rate_limit_rpm == 50
        assert create_dto.rate_limit_rpd == 5000
        assert create_dto.owner == "user-002"
        assert create_dto.tenant == "tenant-xyz"
        assert create_dto.policy == '{"allow": ["read"]}'

    @pytest.mark.asyncio
    async def test_non_admin_denied(
        self,
        op_ctx: OperationContext,
    ) -> None:
        """Non-admin should get 403."""
        # 直接测试 _require_admin 依赖
        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _require_admin(op_ctx)

        assert exc_info.value.status_code == 403
        assert "无权限" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_api_key_error_returns_400(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """APIKeyError from service should be caught and re-raised as 400."""
        data = AdminCreateAPIKeyRequest(app_type="app", app_id="app-1")
        mock_service.create_key.side_effect = APIKeyError(
            code=400004, message="env mismatch"
        )

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_key_admin(data, _op_ctx=admin_ctx, service=mock_service)

        assert exc_info.value.status_code == 400
        assert "env mismatch" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_service_exception_returns_500(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Unexpected service exception should return 500 without leaking details."""
        data = AdminCreateAPIKeyRequest(app_type="app", app_id="app-1")
        mock_service.create_key.side_effect = ValueError("db error")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_key_admin(data, _op_ctx=admin_ctx, service=mock_service)

        assert exc_info.value.status_code == 500
        assert "创建 API Key 失败" in str(exc_info.value.detail)
        assert "db error" not in str(exc_info.value.detail)


# ═════════════════════════════════════════════
# update_api_key_config
# ═════════════════════════════════════════════


class TestUpdateAPIKeyConfig:
    """Tests for PUT /admin/api-keys/{prefix}/config endpoint."""

    @pytest.mark.asyncio
    async def test_admin_can_update_app_id_only(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Admin can update only app_id."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        mock_service.update_key_by_prefix.return_value = sample_key_response
        data = UpdateAPIKeyConfigRequest(app_id="new-app-id")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await update_api_key_config(
                "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        assert result.message == "API Key 配置已更新"
        mock_service.update_key_by_prefix.assert_called_once()
        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        assert update_dto.app_id == "new-app-id"

    @pytest.mark.asyncio
    async def test_admin_can_update_rate_limit(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Admin can update rate_limit fields."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        mock_service.update_key_by_prefix.return_value = sample_key_response
        data = UpdateAPIKeyConfigRequest(rate_limit_rpm=100, rate_limit_rpd=10000)

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await update_api_key_config(
                "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        assert update_dto.rate_limit_rpm == 100
        assert update_dto.rate_limit_rpd == 10000

    @pytest.mark.asyncio
    async def test_admin_can_update_owner_and_tenant(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Admin can update owner and tenant."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        mock_service.update_key_by_prefix.return_value = sample_key_response
        data = UpdateAPIKeyConfigRequest(owner="new-owner", tenant="new-tenant")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await update_api_key_config(
                "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        assert update_dto.owner == "new-owner"
        assert update_dto.tenant == "new-tenant"

    @pytest.mark.asyncio
    async def test_admin_can_update_policy(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Admin can update policy."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        mock_service.update_key_by_prefix.return_value = sample_key_response
        data = UpdateAPIKeyConfigRequest(policy='{"allowed_bots": ["bot-1"]}')

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await update_api_key_config(
                "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        assert update_dto.policy == '{"allowed_bots": ["bot-1"]}'

    @pytest.mark.asyncio
    async def test_admin_can_update_multiple_fields(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Admin can update multiple fields at once."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        mock_service.update_key_by_prefix.return_value = sample_key_response
        data = UpdateAPIKeyConfigRequest(
            app_id="new-app-id",
            app_type="app",
            rate_limit_rpm=200,
            owner="new-owner",
        )

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await update_api_key_config(
                "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        assert update_dto.app_id == "new-app-id"
        assert update_dto.app_type == "app"
        assert update_dto.rate_limit_rpm == 200
        assert update_dto.owner == "new-owner"

    @pytest.mark.asyncio
    async def test_non_admin_denied(
        self,
        op_ctx: OperationContext,
    ) -> None:
        """Non-admin should get 403."""
        # 直接测试 _require_admin 依赖
        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _require_admin(op_ctx)

        assert exc_info.value.status_code == 403
        assert "无权限" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_all_fields_empty_raises_400(
        self,
        admin_ctx: OperationContext,
    ) -> None:
        """All fields empty → 400."""
        data = UpdateAPIKeyConfigRequest()

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_api_key_config(
                    "sk-abc", data, _op_ctx=admin_ctx, service=AsyncMock()
                )

        assert exc_info.value.status_code == 400
        assert "至少需要一个更新字段" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_key_not_found_raises_404(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Key not found → 404."""
        mock_service.get_key_by_prefix.return_value = None
        data = UpdateAPIKeyConfigRequest(app_id="new-app-id")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_api_key_config(
                    "sk-xxx", data, _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 404
        assert "不存在" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_update_returns_service_result(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Update returns whatever the service returns (no redundant 404 check)."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        mock_service.update_key_by_prefix.return_value = sample_key_response
        data = UpdateAPIKeyConfigRequest(app_id="new-app-id")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await update_api_key_config(
                "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        assert result.data == sample_key_response


# ═════════════════════════════════════════════
# get_key_admin
# ═════════════════════════════════════════════


class TestGetKeyAdmin:
    """Tests for GET /admin/api-keys/{api_key_prefix} endpoint."""

    @pytest.mark.asyncio
    async def test_get_key_found(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Admin can query any key by prefix."""
        mock_service.get_key_by_prefix.return_value = sample_key_response

        result = await get_key_admin("sk-abc", _op_ctx=admin_ctx, service=mock_service)

        assert isinstance(result, ApiResponse)
        assert result.data == sample_key_response
        mock_service.get_key_by_prefix.assert_called_once_with("sk-abc", admin_ctx)

    @pytest.mark.asyncio
    async def test_get_key_not_found(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Key not found → 404."""
        mock_service.get_key_by_prefix.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_key_admin("sk-xxx", _op_ctx=admin_ctx, service=mock_service)

        assert exc_info.value.status_code == 404
        assert "不存在" in str(exc_info.value.detail)


# ═════════════════════════════════════════════
# update_key_status_admin
# ═════════════════════════════════════════════


class TestUpdateKeyStatusAdmin:
    """Tests for PATCH /admin/api-keys/{api_key_prefix}/status endpoint."""

    @pytest.mark.asyncio
    async def test_activate_success(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Activate action should call activate_by_prefix."""
        mock_service.activate_by_prefix.return_value = sample_key_response
        data = AdminStatusActionRequest(action="activate")

        result = await update_key_status_admin(
            "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
        )

        assert isinstance(result, ApiResponse)
        assert result.message == "API Key 已启用"
        mock_service.activate_by_prefix.assert_called_once_with("sk-abc", admin_ctx)

    @pytest.mark.asyncio
    async def test_deactivate_success(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Deactivate action should call deactivate_by_prefix."""
        mock_service.deactivate_by_prefix.return_value = sample_key_response
        data = AdminStatusActionRequest(action="deactivate")

        result = await update_key_status_admin(
            "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
        )

        assert isinstance(result, ApiResponse)
        assert result.message == "API Key 已停用"
        mock_service.deactivate_by_prefix.assert_called_once_with("sk-abc", admin_ctx)

    @pytest.mark.asyncio
    async def test_revoke_success(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Revoke action should call revoke_by_prefix."""
        mock_service.revoke_by_prefix.return_value = sample_key_response
        data = AdminStatusActionRequest(action="revoke")

        result = await update_key_status_admin(
            "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
        )

        assert isinstance(result, ApiResponse)
        assert result.message == "API Key 已吊销"
        mock_service.revoke_by_prefix.assert_called_once_with("sk-abc", admin_ctx)

    @pytest.mark.asyncio
    async def test_api_key_error_returns_400(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """APIKeyError from service → 400 with error message."""
        mock_service.activate_by_prefix.side_effect = APIKeyError(
            code=1001, message="Key is already ACTIVE"
        )
        data = AdminStatusActionRequest(action="activate")

        with pytest.raises(HTTPException) as exc_info:
            await update_key_status_admin(
                "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert exc_info.value.status_code == 400
        assert "Key is already ACTIVE" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_key_not_found_returns_404(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Service returns None → 404."""
        mock_service.activate_by_prefix.return_value = None
        data = AdminStatusActionRequest(action="activate")

        with pytest.raises(HTTPException) as exc_info:
            await update_key_status_admin(
                "sk-abc", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────
# Shared fixtures for allowed-bots admin tests
# ─────────────────────────────────────────────


@pytest.fixture
def app_key_response() -> APIKeyResponse:
    """App-type APIKeyResponse with empty policy."""
    from datetime import datetime

    return APIKeyResponse(
        id=100,
        app_id="my-app",
        app_type="app",
        key_name="app-key",
        api_key_prefix="sk-app",
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="user-001",
        tenant="tenant-1",
        env="test",
        creator="user-001",
        modifier=None,
        policy='{"allowed_bots":[]}',
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


# ═════════════════════════════════════════════
# get_allowed_bots_admin
# ═════════════════════════════════════════════


class TestGetAllowedBotsAdmin:
    """Tests for GET /admin/api-keys/{prefix}/allowed-bots endpoint."""

    @pytest.mark.asyncio
    async def test_get_allowed_bots_success(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """Admin can get allowed_bots for an app key."""
        mock_service.get_key_by_prefix.return_value = app_key_response

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await get_allowed_bots_admin(
                "sk-app", _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        assert result.data == {"allowed_bots": []}

    @pytest.mark.asyncio
    async def test_key_not_found_raises_404(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Key not found -> 404."""
        mock_service.get_key_by_prefix.return_value = None

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_allowed_bots_admin(
                    "sk-xxx", _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_non_app_key_raises_400(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """Non-app key -> 400."""
        bot_key = app_key_response.model_copy(update={"app_type": "bot"})
        mock_service.get_key_by_prefix.return_value = bot_key

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_allowed_bots_admin(
                    "sk-bot", _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_none_sentinel_filtered(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """NONE sentinel should be filtered from result."""
        none_key = app_key_response.model_copy(
            update={"policy": '{"allowed_bots":["NONE"]}'}
        )
        mock_service.get_key_by_prefix.return_value = none_key

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await get_allowed_bots_admin(
                "sk-app", _op_ctx=admin_ctx, service=mock_service
            )

        assert result.data == {"allowed_bots": []}

    @pytest.mark.asyncio
    async def test_mixed_none_and_real_bots(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """NONE is filtered but real bot IDs remain."""
        mixed_key = app_key_response.model_copy(
            update={"policy": '{"allowed_bots":["NONE","bot-1:entity-789"]}'}
        )
        mock_service.get_key_by_prefix.return_value = mixed_key

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await get_allowed_bots_admin(
                "sk-app", _op_ctx=admin_ctx, service=mock_service
            )

        assert result.data == {"allowed_bots": ["bot-1:entity-789"]}


# ═════════════════════════════════════════════
# grant_allowed_bot_admin
# ═════════════════════════════════════════════


class TestGrantAllowedBotAdmin:
    """Tests for POST /admin/api-keys/{prefix}/allowed-bots/grant endpoint."""

    @pytest.mark.asyncio
    async def test_grant_new_bot_success(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """Admin can grant a new bot to an app key."""
        mock_service.get_key_by_prefix.return_value = app_key_response
        updated = app_key_response.model_copy(
            update={"policy": '{"allowed_bots":["new-bot:entity-001"]}'}
        )
        mock_service.update_key_by_prefix.return_value = updated
        data = AdminBotIdRequest(bot_id="new-bot:entity-001")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await grant_allowed_bot_admin(
                "sk-app", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        assert result.message == "allowed_bots 已授权"
        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        assert "new-bot:entity-001" in update_dto.policy

    @pytest.mark.asyncio
    async def test_grant_replaces_none_sentinel(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """When policy is ['NONE'], grant clears NONE and appends the real bot_id."""
        none_key = app_key_response.model_copy(
            update={"policy": '{"allowed_bots":["NONE"]}'}
        )
        mock_service.get_key_by_prefix.return_value = none_key
        updated = none_key.model_copy(
            update={"policy": '{"allowed_bots":["new-bot:entity-001"]}'}
        )
        mock_service.update_key_by_prefix.return_value = updated
        data = AdminBotIdRequest(bot_id="new-bot:entity-001")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await grant_allowed_bot_admin(
                "sk-app", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        import json

        call_args = mock_service.update_key_by_prefix.call_args
        update_dto = call_args[0][1]
        parsed_policy = json.loads(update_dto.policy)
        assert "NONE" not in parsed_policy["allowed_bots"]
        assert "new-bot:entity-001" in parsed_policy["allowed_bots"]

    @pytest.mark.asyncio
    async def test_grant_existing_bot_idempotent(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """Granting an already-granted bot is idempotent."""
        existing_key = app_key_response.model_copy(
            update={"policy": '{"allowed_bots":["bot-1:entity-789"]}'}
        )
        mock_service.get_key_by_prefix.return_value = existing_key
        mock_service.update_key_by_prefix.return_value = existing_key
        data = AdminBotIdRequest(bot_id="bot-1:entity-789")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await grant_allowed_bot_admin(
                "sk-app", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)

    @pytest.mark.asyncio
    async def test_key_not_found_raises_404(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Key not found -> 404."""
        mock_service.get_key_by_prefix.return_value = None
        data = AdminBotIdRequest(bot_id="bot-1:entity-001")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await grant_allowed_bot_admin(
                    "sk-xxx", data, _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_non_app_key_raises_400(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Non-app key -> 400."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        data = AdminBotIdRequest(bot_id="bot-1:entity-001")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await grant_allowed_bot_admin(
                    "sk-bot", data, _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_bot_id_raises_400(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """Invalid bot_id format -> 400."""
        mock_service.get_key_by_prefix.return_value = app_key_response
        data = AdminBotIdRequest(bot_id="invalid-bot-id")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await grant_allowed_bot_admin(
                    "sk-app", data, _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 400
        assert "bot_id 格式无效" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_update_returns_none_raises_404(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """update_key_by_prefix returns None -> 404."""
        mock_service.get_key_by_prefix.return_value = app_key_response
        mock_service.update_key_by_prefix.return_value = None
        data = AdminBotIdRequest(bot_id="bot-1:entity-001")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await grant_allowed_bot_admin(
                    "sk-app", data, _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 404


# ═════════════════════════════════════════════
# revoke_allowed_bot_admin
# ═════════════════════════════════════════════


class TestRevokeAllowedBotAdmin:
    """Tests for POST /admin/api-keys/{prefix}/allowed-bots/revoke endpoint."""

    @pytest.mark.asyncio
    async def test_revoke_existing_bot_success(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """Admin can revoke an existing bot from an app key."""
        granted_key = app_key_response.model_copy(
            update={"policy": '{"allowed_bots":["bot-1:entity-789"]}'}
        )
        mock_service.get_key_by_prefix.return_value = granted_key
        revoked = granted_key.model_copy(update={"policy": '{"allowed_bots":[]}'})
        mock_service.update_key_by_prefix.return_value = revoked
        data = AdminBotIdRequest(bot_id="bot-1:entity-789")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            result = await revoke_allowed_bot_admin(
                "sk-app", data, _op_ctx=admin_ctx, service=mock_service
            )

        assert isinstance(result, ApiResponse)
        assert result.message == "allowed_bots 已撤销"

    @pytest.mark.asyncio
    async def test_revoke_non_existent_bot_raises_404(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """Revoking a bot_id not in allowed_bots -> 404."""
        mock_service.get_key_by_prefix.return_value = app_key_response
        data = AdminBotIdRequest(bot_id="absent-bot:entity-999")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_allowed_bot_admin(
                    "sk-app", data, _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_key_not_found_raises_404(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
    ) -> None:
        """Key not found -> 404."""
        mock_service.get_key_by_prefix.return_value = None
        data = AdminBotIdRequest(bot_id="bot-1:entity-001")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_allowed_bot_admin(
                    "sk-xxx", data, _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_non_app_key_raises_400(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        sample_key_response: APIKeyResponse,
    ) -> None:
        """Non-app key -> 400."""
        mock_service.get_key_by_prefix.return_value = sample_key_response
        data = AdminBotIdRequest(bot_id="bot-1:entity-001")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_allowed_bot_admin(
                    "sk-bot", data, _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_bot_id_raises_400(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """Invalid bot_id format -> 400."""
        mock_service.get_key_by_prefix.return_value = app_key_response
        data = AdminBotIdRequest(bot_id="invalid-id")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_allowed_bot_admin(
                    "sk-app", data, _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 400
        assert "bot_id 格式无效" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_update_returns_none_raises_404(
        self,
        admin_ctx: OperationContext,
        mock_service: AsyncMock,
        app_key_response: APIKeyResponse,
    ) -> None:
        """update_key_by_prefix returns None -> 404."""
        granted_key = app_key_response.model_copy(
            update={"policy": '{"allowed_bots":["bot-1:entity-789"]}'}
        )
        mock_service.get_key_by_prefix.return_value = granted_key
        mock_service.update_key_by_prefix.return_value = None
        data = AdminBotIdRequest(bot_id="bot-1:entity-789")

        with patch(
            "secbaas.adapters.web.routers.admin.api_gateway_router.is_admin",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_allowed_bot_admin(
                    "sk-app", data, _op_ctx=admin_ctx, service=mock_service
                )

        assert exc_info.value.status_code == 404
