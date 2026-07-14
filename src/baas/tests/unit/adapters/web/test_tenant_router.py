"""Unit tests for tenant_router.

Covers all 6 endpoints with happy-path, 404, and edge cases:
- GET /api/v1/tenants — list_tenants
- GET /api/v1/tenants/{name} — get_tenant (found + 404)
- GET /api/v1/tenants/{name}/config — get_tenant_config (found + 404)
- POST /api/v1/tenants — create_tenant
- PUT /api/v1/tenants/{name} — update_tenant (found + 404)
- DELETE /api/v1/tenants/{name} — delete_tenant (success + 404)

Uses direct async function calls with MagicMock service passed as `service=`
keyword argument (matching FastAPI Depends parameter name). Also patches
get_current_env in the service layer (not the router).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from secbaas.community.adapters.web.routers.config_management.tenant_router import (
    create_tenant,
    delete_tenant,
    get_tenant,
    get_tenant_config,
    list_tenants,
    update_tenant,
)
from secbaas.community.api import ApiResponse, SuccessResponse
from secbaas.community.api.tenant_manage import (
    TenantConfig,
    TenantCreate,
    TenantListResponse,
    TenantResponse,
    TenantUpdate,
)

# ==================== Helpers ====================


def _make_tenant_response(
    name: str = "test_tenant",
    description: str | None = "A test tenant",
    env: str = "dev",
    extra_config: TenantConfig | None = None,
    creator: str = "user1",
    modifier: str = "user1",
) -> TenantResponse:
    """Build a TenantResponse for test assertions."""
    now = datetime.now(tz=UTC)
    return TenantResponse(
        name=name,
        description=description,
        env=env,
        extra_config=extra_config,
        creator=creator,
        modifier=modifier,
        gmt_create=now,
        gmt_modified=now,
    )


def _make_tenant_list(items: list[TenantResponse] | None = None) -> TenantListResponse:
    """Build a TenantListResponse for test assertions."""
    if items is None:
        items = [_make_tenant_response()]
    return TenantListResponse(
        items=items,
        total=len(items),
        page=1,
        page_size=20,
    )


def _make_create_request(
    name: str = "new_tenant",
    description: str | None = "A new tenant",
    extra_config: TenantConfig | None = None,
    operator: str | None = "admin",
) -> TenantCreate:
    """Build a valid TenantCreate for tests."""
    return TenantCreate(
        name=name,
        description=description,
        extra_config=extra_config,
        operator=operator,
    )


def _make_update_request(
    description: str | None = "Updated description",
    extra_config: TenantConfig | None = None,
    operator: str | None = "modifier_user",
) -> TenantUpdate:
    """Build a valid TenantUpdate for tests."""
    return TenantUpdate(
        description=description,
        extra_config=extra_config,
        operator=operator,
    )


# ==================== list_tenants ====================


class TestListTenants:
    async def test_returns_paginated_tenant_list(self):
        """Happy path: returns ApiResponse containing TenantListResponse."""
        mock_svc = MagicMock()
        mock_svc.list_tenants.return_value = _make_tenant_list(
            items=[_make_tenant_response(name="t1")]
        )

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await list_tenants(page=1, page_size=10, service=mock_svc)

        assert isinstance(response, ApiResponse)
        assert response.code == 0
        assert response.data.total == 1
        assert response.data.items[0].name == "t1"
        assert response.data.page == 1
        assert response.data.page_size == 20  # model default — our mock sets it

    async def test_forwards_pagination_parameters(self):
        """Ensure page and page_size are forwarded to the service."""
        mock_svc = MagicMock()
        mock_svc.list_tenants.return_value = _make_tenant_list()

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            await list_tenants(page=3, page_size=50, service=mock_svc)

        mock_svc.list_tenants.assert_called_once_with(page=3, page_size=50)

    async def test_default_pagination_parameters(self):
        """Verify defaults when page and page_size are omitted."""
        mock_svc = MagicMock()
        mock_svc.list_tenants.return_value = _make_tenant_list()

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            await list_tenants(service=mock_svc)

        mock_svc.list_tenants.assert_called_once_with(page=1, page_size=20)

    async def test_returns_empty_list(self):
        """When service returns no tenants, response contains empty items."""
        mock_svc = MagicMock()
        mock_svc.list_tenants.return_value = TenantListResponse(
            items=[], total=0, page=1, page_size=20
        )

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await list_tenants(service=mock_svc)

        assert response.data.items == []
        assert response.data.total == 0


# ==================== get_tenant ====================


class TestGetTenant:
    async def test_returns_tenant_when_found(self):
        """Happy path: tenant exists → ApiResponse with tenant data."""
        mock_svc = MagicMock()
        mock_svc.get_tenant_by_name.return_value = _make_tenant_response(
            name="found_tenant"
        )

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await get_tenant(name="found_tenant", service=mock_svc)

        assert isinstance(response, ApiResponse)
        assert response.data.name == "found_tenant"
        assert response.data.env == "dev"

    async def test_raises_404_when_tenant_not_found(self):
        """Service returns None → HTTPException(404)."""
        mock_svc = MagicMock()
        mock_svc.get_tenant_by_name.return_value = None

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_tenant(name="missing", service=mock_svc)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TENANT_NOT_FOUND"
        assert "missing" in exc_info.value.detail["message"]

    async def test_forwards_name_to_service(self):
        """Ensure the name path parameter reaches the service."""
        mock_svc = MagicMock()
        mock_svc.get_tenant_by_name.return_value = _make_tenant_response(name="xyz")

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            await get_tenant(name="xyz", service=mock_svc)

        mock_svc.get_tenant_by_name.assert_called_once_with("xyz")


# ==================== get_tenant_config ====================


class TestGetTenantConfig:
    async def test_returns_config_when_found(self):
        """Happy path: returns ApiResponse containing TenantConfig."""
        mock_svc = MagicMock()
        mock_svc.get_tenant_config.return_value = TenantConfig(
            default_template_uuid="TPL-abc"
        )

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await get_tenant_config(name="t1", service=mock_svc)

        assert isinstance(response, ApiResponse)
        assert response.data.default_template_uuid == "TPL-abc"

    async def test_returns_config_when_empty(self):
        """TenantConfig with no fields set is still valid data."""
        mock_svc = MagicMock()
        mock_svc.get_tenant_config.return_value = TenantConfig()

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await get_tenant_config(name="t1", service=mock_svc)

        assert response.data.default_template_uuid is None

    async def test_raises_404_when_config_not_found(self):
        """Service returns None → HTTPException(404)."""
        mock_svc = MagicMock()
        mock_svc.get_tenant_config.return_value = None

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_tenant_config(name="missing", service=mock_svc)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TENANT_NOT_FOUND"

    async def test_forwards_name_to_service(self):
        """Ensure the name path parameter reaches the service."""
        mock_svc = MagicMock()
        mock_svc.get_tenant_config.return_value = TenantConfig()

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            await get_tenant_config(name="target_tenant", service=mock_svc)

        mock_svc.get_tenant_config.assert_called_once_with("target_tenant")


# ==================== create_tenant ====================


class TestCreateTenant:
    async def test_creates_tenant_successfully(self):
        """Happy path: create tenant and return ApiResponse."""
        mock_svc = MagicMock()
        mock_svc.create_tenant.return_value = _make_tenant_response(name="new_tenant")
        request = _make_create_request(name="new_tenant")

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await create_tenant(request=request, service=mock_svc)

        assert isinstance(response, ApiResponse)
        assert response.data.name == "new_tenant"

    async def test_forwards_request_data_to_service(self):
        """Ensure the full TenantCreate object is passed to the service."""
        mock_svc = MagicMock()
        mock_svc.create_tenant.return_value = _make_tenant_response()
        request = _make_create_request(
            name="t2", description="desc text", operator="op"
        )

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            await create_tenant(request=request, service=mock_svc)

        mock_svc.create_tenant.assert_called_once_with(data=request)

    async def test_creates_tenant_with_extra_config(self):
        """Tenant with extra_config (template UUID)."""
        cfg = TenantConfig(default_template_uuid="TPL-xyz")
        mock_svc = MagicMock()
        mock_svc.create_tenant.return_value = _make_tenant_response(
            name="t3", extra_config=cfg
        )
        request = _make_create_request(name="t3", extra_config=cfg)

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await create_tenant(request=request, service=mock_svc)

        assert response.data.extra_config.default_template_uuid == "TPL-xyz"

    async def test_creates_tenant_without_operator(self):
        """TenantCreate without operator still works."""
        mock_svc = MagicMock()
        mock_svc.create_tenant.return_value = _make_tenant_response(name="t4")
        request = _make_create_request(name="t4", operator=None)

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await create_tenant(request=request, service=mock_svc)

        assert response.data.name == "t4"


# ==================== update_tenant ====================


class TestUpdateTenant:
    async def test_updates_tenant_successfully(self):
        """Happy path: update existing tenant."""
        mock_svc = MagicMock()
        mock_svc.update_tenant.return_value = _make_tenant_response(
            name="updated_tenant"
        )
        request = _make_update_request()

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await update_tenant(
                name="updated_tenant", request=request, service=mock_svc
            )

        assert isinstance(response, ApiResponse)
        assert response.data.name == "updated_tenant"

    async def test_raises_404_when_tenant_not_found(self):
        """Service returns None → HTTPException(404)."""
        mock_svc = MagicMock()
        mock_svc.update_tenant.return_value = None
        request = _make_update_request()

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_tenant(name="missing", request=request, service=mock_svc)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TENANT_NOT_FOUND"

    async def test_forwards_name_and_data_to_service(self):
        """Ensure name and TenantUpdate are both forwarded."""
        mock_svc = MagicMock()
        mock_svc.update_tenant.return_value = _make_tenant_response()
        request = _make_update_request(description="new desc")

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            await update_tenant(name="my_tenant", request=request, service=mock_svc)

        mock_svc.update_tenant.assert_called_once_with(name="my_tenant", data=request)

    async def test_updates_only_description(self):
        """Update with description only (no extra_config)."""
        mock_svc = MagicMock()
        mock_svc.update_tenant.return_value = _make_tenant_response(
            description="new desc only"
        )
        request = _make_update_request(description="new desc only", extra_config=None)

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await update_tenant(name="t", request=request, service=mock_svc)

        assert response.data.description == "new desc only"

    async def test_updates_only_extra_config(self):
        """Update with extra_config only (no description)."""
        cfg = TenantConfig(default_template_uuid="TPL-new")
        mock_svc = MagicMock()
        mock_svc.update_tenant.return_value = _make_tenant_response(extra_config=cfg)
        request = _make_update_request(description=None, extra_config=cfg)

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await update_tenant(name="t", request=request, service=mock_svc)

        assert response.data.extra_config.default_template_uuid == "TPL-new"


# ==================== delete_tenant ====================


class TestDeleteTenant:
    async def test_deletes_tenant_successfully(self):
        """Happy path: soft-delete returns success."""
        mock_svc = MagicMock()
        mock_svc.soft_delete_tenant.return_value = True

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await delete_tenant(
                name="to_delete", operator="admin", service=mock_svc
            )

        assert isinstance(response, ApiResponse)
        assert isinstance(response.data, SuccessResponse)
        assert response.data.success is True

    async def test_raises_404_when_tenant_not_found(self):
        """Service returns False → HTTPException(404)."""
        mock_svc = MagicMock()
        mock_svc.soft_delete_tenant.return_value = False

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_tenant(name="missing", operator="admin", service=mock_svc)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TENANT_NOT_FOUND"

    async def test_forwards_name_and_operator_to_service(self):
        """Ensure name and operator params reach the service."""
        mock_svc = MagicMock()
        mock_svc.soft_delete_tenant.return_value = True

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            await delete_tenant(name="t1", operator="op123", service=mock_svc)

        mock_svc.soft_delete_tenant.assert_called_once_with(name="t1", operator="op123")

    async def test_operator_defaults_to_unknown(self):
        """When operator is not provided, it defaults to 'unknown'."""
        mock_svc = MagicMock()
        mock_svc.soft_delete_tenant.return_value = True

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            await delete_tenant(name="t1", service=mock_svc)

        mock_svc.soft_delete_tenant.assert_called_once_with(
            name="t1", operator="unknown"
        )

    async def test_delete_response_message(self):
        """Verify the success response message is correct."""
        mock_svc = MagicMock()
        mock_svc.soft_delete_tenant.return_value = True

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            response = await delete_tenant(name="t", service=mock_svc)

        assert response.data.message == "Tenant deleted"


# ==================== Error detail structure ====================


class TestErrorResponses:
    """Cross-cutting tests for HTTP error detail structure."""

    @pytest.mark.parametrize(
        "handler,handler_args,patch_target,return_value,expected_msg_part",
        [
            (
                get_tenant,
                {"name": "x"},
                "get_tenant_by_name",
                None,
                "x",
            ),
            (
                get_tenant_config,
                {"name": "y"},
                "get_tenant_config",
                None,
                "y",
            ),
            (
                update_tenant,
                {"name": "z", "request": _make_update_request()},
                "update_tenant",
                None,
                "z",
            ),
            (
                delete_tenant,
                {"name": "w", "operator": "op"},
                "soft_delete_tenant",
                False,
                "w",
            ),
        ],
    )
    async def test_404_detail_contains_error_code_and_tenant_name(
        self, handler, handler_args, patch_target, return_value, expected_msg_part
    ):
        """All 404s share the same error_code and include the tenant name."""
        mock_svc = MagicMock()
        setattr(mock_svc, patch_target, MagicMock(return_value=return_value))

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="pre",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await handler(service=mock_svc, **handler_args)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TENANT_NOT_FOUND"
        assert expected_msg_part in exc_info.value.detail["message"]


# ==================== Request body models ====================


class TestRequestModels:
    """Smoke tests for TenantCreate and TenantUpdate Pydantic models."""

    def test_tenant_create_minimal(self):
        """Minimal TenantCreate — only name required."""
        tc = TenantCreate(name="minimal")
        assert tc.name == "minimal"
        assert tc.description is None
        assert tc.operator is None

    def test_tenant_create_full(self):
        """Full TenantCreate with all fields."""
        cfg = TenantConfig(default_template_uuid="TPL-1")
        tc = TenantCreate(
            name="full", description="desc", extra_config=cfg, operator="op"
        )
        assert tc.name == "full"
        assert tc.description == "desc"
        assert tc.extra_config.default_template_uuid == "TPL-1"
        assert tc.operator == "op"

    def test_tenant_update_minimal(self):
        """Minimal TenantUpdate — all optional fields."""
        tu = TenantUpdate()
        assert tu.description is None
        assert tu.extra_config is None
        assert tu.operator is None

    def test_tenant_update_full(self):
        """TenantUpdate with all fields set."""
        cfg = TenantConfig(default_template_uuid="TPL-2")
        tu = TenantUpdate(description="d", extra_config=cfg, operator="op")
        assert tu.description == "d"
        assert tu.extra_config.default_template_uuid == "TPL-2"
        assert tu.operator == "op"
