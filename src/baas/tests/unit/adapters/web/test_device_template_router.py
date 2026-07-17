"""Tests for device_template_router.

Tests all 8 REST API endpoints for device template management:
- GET /api/v1/device-templates (list with tenant + status filter)
- GET /api/v1/device-templates/online (list ONLINE templates)
- GET /api/v1/device-templates/by-template-id/{template_id} (get by template_id)
- GET /api/v1/device-templates/resolve (resolve default/explicit template)
- GET /api/v1/device-templates/{template_uuid} (get by UUID)
- POST /api/v1/device-templates (create)
- POST /api/v1/device-templates/{template_uuid}/status-transitions (status transition)
- PUT /api/v1/device-templates/{template_uuid} (update)
- POST /api/v1/device-templates/{template_uuid}/delete (soft delete)
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from secbaas.community.adapters.web.routers.config_management.device_template_router import (
    DeleteTemplateRequest,
    StatusTransitionRequest,
    create_template,
    delete_template,
    get_template,
    get_template_by_id,
    list_online_templates,
    list_templates,
    resolve_template,
    transition_template_status,
    update_template,
)
from secbaas.community.api import OperationContext
from secbaas.community.api.template_manage import (
    DeviceTemplateResponse,
    TemplateCreate,
    TemplateListResponse,
    TemplateStatus,
    TemplateUpdate,
)
from secbaas.community.api.template_manage._models import ArcaTemplateConfig

# ==================== Helpers ====================

_OP_CTX = OperationContext(operator="test_user", env="dev")


def _make_template_response(
    id: int = 1,
    template_id: int = 100,
    template_uuid: str = "TEMPLATE-abc123",
    tenant: str = "test_tenant",
    name: str = "openclaw",
    status: str = "ONLINE",
) -> DeviceTemplateResponse:
    """Build a DeviceTemplateResponse for test assertions."""
    now = datetime.now(tz=UTC)
    return DeviceTemplateResponse(
        id=id,
        template_id=template_id,
        type="ARCA",
        template_uuid=template_uuid,
        tenant=tenant,
        name=name,
        description="Test template",
        status=status,
        config=ArcaTemplateConfig(
            type="ARCA",
            base_url="https://arca.test",
            api_key="key123",
        ),
        creator="user1",
        modifier="user1",
        gmt_create=now,
        gmt_modified=now,
    )


def _make_create_request() -> TemplateCreate:
    """Build a valid TemplateCreate for tests."""
    return TemplateCreate(
        template_id=100,
        type="ARCA",
        name="openclaw",
        operator="test_user",
        config=ArcaTemplateConfig(
            type="ARCA",
            base_url="https://arca.test",
            api_key="key123",
        ),
    )


def _make_update_request() -> TemplateUpdate:
    """Build a valid TemplateUpdate for tests."""
    return TemplateUpdate(
        name="updated-name",
        operator="test_user",
    )


# ==================== Model Tests ====================


class TestStatusTransitionRequest:
    """Tests for StatusTransitionRequest model."""

    def test_model_creation(self) -> None:
        """Test StatusTransitionRequest can be created."""
        request = StatusTransitionRequest(
            current_status=TemplateStatus.CREATED,
            new_status=TemplateStatus.AUDITED,
        )
        assert request.current_status == TemplateStatus.CREATED
        assert request.new_status == TemplateStatus.AUDITED

    def test_enum_values(self) -> None:
        """Test TemplateStatus enum values."""
        request = StatusTransitionRequest(
            current_status=TemplateStatus.ONLINE,
            new_status=TemplateStatus.OFFLINE,
        )
        assert request.current_status.value == "ONLINE"
        assert request.new_status.value == "OFFLINE"


class TestDeleteTemplateRequest:
    """Tests for DeleteTemplateRequest model."""

    def test_model_creation(self) -> None:
        """Test DeleteTemplateRequest can be created."""
        request = DeleteTemplateRequest(operator="admin")
        assert request.operator == "admin"


# ==================== GET list_templates ====================


class TestListTemplates:
    """Tests for list_templates endpoint."""

    @pytest.mark.asyncio
    async def test_list_templates_success(self) -> None:
        """Test listing templates with optional status filter."""
        mock_svc = MagicMock()
        expected = TemplateListResponse(items=[], total=0, page=1, page_size=20)
        mock_svc.list_templates.return_value = expected

        result = await list_templates(
            tenant="test_tenant", op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        assert result.data == expected
        mock_svc.list_templates.assert_called_once_with(
            tenant="test_tenant",
            status=None,
            page=1,
            page_size=20,
        )

    @pytest.mark.asyncio
    async def test_list_templates_with_status_filter(self) -> None:
        """Test listing templates filtered by status."""
        mock_svc = MagicMock()
        expected = TemplateListResponse(items=[], total=0, page=1, page_size=20)
        mock_svc.list_templates.return_value = expected

        result = await list_templates(
            tenant="test_tenant",
            status=TemplateStatus.ONLINE,
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        mock_svc.list_templates.assert_called_once_with(
            tenant="test_tenant",
            status=TemplateStatus.ONLINE,
            page=1,
            page_size=20,
        )

    @pytest.mark.asyncio
    async def test_list_templates_pagination(self) -> None:
        """Test listing templates with custom pagination."""
        mock_svc = MagicMock()
        expected = TemplateListResponse(items=[], total=50, page=3, page_size=10)
        mock_svc.list_templates.return_value = expected

        result = await list_templates(
            tenant="test_tenant",
            page=3,
            page_size=10,
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        mock_svc.list_templates.assert_called_once_with(
            tenant="test_tenant",
            status=None,
            page=3,
            page_size=10,
        )

    @pytest.mark.asyncio
    async def test_list_templates_with_items(self) -> None:
        """Test listing templates returns items in data."""
        mock_svc = MagicMock()
        tmpl = _make_template_response()
        expected = TemplateListResponse(items=[tmpl], total=1, page=1, page_size=20)
        mock_svc.list_templates.return_value = expected

        result = await list_templates(
            tenant="test_tenant", op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        assert result.data is not None
        assert result.data.total == 1
        assert len(result.data.items) == 1
        assert result.data.items[0].template_uuid == "TEMPLATE-abc123"


# ==================== GET list_online_templates ====================


class TestListOnlineTemplates:
    """Tests for list_online_templates endpoint."""

    @pytest.mark.asyncio
    async def test_list_online_success(self) -> None:
        """Test listing online templates."""
        mock_svc = MagicMock()
        tmpl = _make_template_response(status="ONLINE")
        expected = TemplateListResponse(items=[tmpl], total=1, page=1, page_size=20)
        mock_svc.list_online_templates.return_value = expected

        result = await list_online_templates(
            tenant="test_tenant", op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        assert result.data == expected
        mock_svc.list_online_templates.assert_called_once_with(
            tenant="test_tenant",
            page=1,
            page_size=20,
        )

    @pytest.mark.asyncio
    async def test_list_online_pagination(self) -> None:
        """Test listing online templates with pagination."""
        mock_svc = MagicMock()
        expected = TemplateListResponse(items=[], total=0, page=2, page_size=50)
        mock_svc.list_online_templates.return_value = expected

        result = await list_online_templates(
            tenant="test_tenant",
            page=2,
            page_size=50,
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        mock_svc.list_online_templates.assert_called_once_with(
            tenant="test_tenant",
            page=2,
            page_size=50,
        )

    @pytest.mark.asyncio
    async def test_list_online_empty(self) -> None:
        """Test listing online templates when none exist."""
        mock_svc = MagicMock()
        expected = TemplateListResponse(items=[], total=0, page=1, page_size=20)
        mock_svc.list_online_templates.return_value = expected

        result = await list_online_templates(
            tenant="empty_tenant", op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        assert result.data is not None
        assert result.data.total == 0
        assert len(result.data.items) == 0


# ==================== GET by-template-id ====================


class TestGetTemplateById:
    """Tests for get_template_by_id endpoint."""

    @pytest.mark.asyncio
    async def test_get_by_template_id_success(self) -> None:
        """Test getting template by global template_id."""
        mock_svc = MagicMock()
        expected = _make_template_response(id=42, template_id=999)
        mock_svc.get_by_template_id.return_value = expected

        result = await get_template_by_id(
            template_id=999, op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        assert result.data == expected
        mock_svc.get_by_template_id.assert_called_once_with(999)

    @pytest.mark.asyncio
    async def test_get_by_template_id_not_found(self) -> None:
        """Test getting template by template_id returns 404 when not found."""
        mock_svc = MagicMock()
        mock_svc.get_by_template_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_template_by_id(
                template_id=99999, op_ctx=_OP_CTX, service=mock_svc
            )

        assert exc_info.value.status_code == 404
        detail = exc_info.value.detail
        assert detail["error_code"] == "TEMPLATE_NOT_FOUND"
        assert "99999" in detail["message"]

    @pytest.mark.asyncio
    async def test_get_by_template_id_zero(self) -> None:
        """Test getting template by template_id=0 (minimum valid)."""
        mock_svc = MagicMock()
        expected = _make_template_response(template_id=0)
        mock_svc.get_by_template_id.return_value = expected

        result = await get_template_by_id(
            template_id=0, op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        mock_svc.get_by_template_id.assert_called_once_with(0)


# ==================== GET resolve_template ====================


class TestResolveTemplate:
    """Tests for resolve_template endpoint."""

    @pytest.mark.asyncio
    async def test_resolve_default_template(self) -> None:
        """Test resolving default template (no template_uuid provided)."""
        mock_svc = MagicMock()
        expected = _make_template_response(template_uuid="DEFAULT-xyz")
        mock_svc.get_default_or_explicit_template.return_value = expected

        result = await resolve_template(
            tenant="test_tenant", op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        assert result.data == expected
        mock_svc.get_default_or_explicit_template.assert_called_once_with(
            tenant="test_tenant",
            template_uuid=None,
        )

    @pytest.mark.asyncio
    async def test_resolve_explicit_template(self) -> None:
        """Test resolving explicit template by UUID."""
        mock_svc = MagicMock()
        expected = _make_template_response(template_uuid="EXPLICIT-abc")
        mock_svc.get_default_or_explicit_template.return_value = expected

        result = await resolve_template(
            tenant="test_tenant",
            template_uuid="EXPLICIT-abc",
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        assert result.data == expected
        mock_svc.get_default_or_explicit_template.assert_called_once_with(
            tenant="test_tenant",
            template_uuid="EXPLICIT-abc",
        )

    @pytest.mark.asyncio
    async def test_resolve_template_not_found(self) -> None:
        """Test resolve_template raises 404 on ValueError."""
        mock_svc = MagicMock()
        mock_svc.get_default_or_explicit_template.side_effect = ValueError(
            "No template configured for tenant"
        )

        with pytest.raises(HTTPException) as exc_info:
            await resolve_template(
                tenant="bad_tenant", op_ctx=_OP_CTX, service=mock_svc
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TEMPLATE_NOT_FOUND"
        assert "No template configured" in exc_info.value.detail["message"]

    @pytest.mark.asyncio
    async def test_resolve_template_not_found_explicit(self) -> None:
        """Test resolve_template raises 404 when explicit UUID not found."""
        mock_svc = MagicMock()
        mock_svc.get_default_or_explicit_template.side_effect = ValueError(
            "Template not found by uuid: MISSING"
        )

        with pytest.raises(HTTPException) as exc_info:
            await resolve_template(
                tenant="test_tenant",
                template_uuid="MISSING",
                op_ctx=_OP_CTX,
                service=mock_svc,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TEMPLATE_NOT_FOUND"


# ==================== GET /{template_uuid} ====================


class TestGetTemplate:
    """Tests for get_template endpoint (by UUID + tenant)."""

    @pytest.mark.asyncio
    async def test_get_template_success(self) -> None:
        """Test getting template by UUID with tenant isolation."""
        mock_svc = MagicMock()
        expected = _make_template_response(template_uuid="UUID-12345")
        mock_svc.get_online_template_by_uuid.return_value = expected

        result = await get_template(
            template_uuid="UUID-12345",
            tenant="test_tenant",
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        assert result.data == expected
        mock_svc.get_online_template_by_uuid.assert_called_once_with(
            "test_tenant", "UUID-12345"
        )

    @pytest.mark.asyncio
    async def test_get_template_not_found(self) -> None:
        """Test get_template returns 404 when not found."""
        mock_svc = MagicMock()
        mock_svc.get_online_template_by_uuid.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_template(
                template_uuid="MISSING",
                tenant="test_tenant",
                op_ctx=_OP_CTX,
                service=mock_svc,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TEMPLATE_NOT_FOUND"
        assert "test_tenant/MISSING" in exc_info.value.detail["message"]

    @pytest.mark.asyncio
    async def test_get_template_different_tenant_not_found(self) -> None:
        """Test tenant isolation: template from other tenant returns 404."""
        mock_svc = MagicMock()
        mock_svc.get_online_template_by_uuid.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_template(
                template_uuid="UUID-12345",
                tenant="other_tenant",
                op_ctx=_OP_CTX,
                service=mock_svc,
            )

        assert exc_info.value.status_code == 404


# ==================== POST create_template ====================


class TestCreateTemplate:
    """Tests for create_template endpoint."""

    @pytest.mark.asyncio
    async def test_create_template_success(self) -> None:
        """Test creating a template with explicit UUID."""
        mock_svc = MagicMock()
        request = _make_create_request()
        request.template_uuid = "MY-UUID-001"
        expected = _make_template_response(template_uuid="MY-UUID-001")
        mock_svc.create_template.return_value = expected

        result = await create_template(
            request=request,
            tenant="test_tenant",
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        assert result.data == expected
        mock_svc.create_template.assert_called_once_with(
            tenant="test_tenant", data=request
        )

    @pytest.mark.asyncio
    async def test_create_template_auto_uuid(self) -> None:
        """Test creating a template with auto-generated UUID (None)."""
        mock_svc = MagicMock()
        request = _make_create_request()
        request.template_uuid = None
        expected = _make_template_response(template_uuid="TEMPLATE-auto-gen")
        mock_svc.create_template.return_value = expected

        result = await create_template(
            request=request,
            tenant="test_tenant",
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        assert result.data.template_uuid == "TEMPLATE-auto-gen"
        mock_svc.create_template.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_template_with_all_fields(self) -> None:
        """Test creating a template with all optional fields."""
        mock_svc = MagicMock()
        request = TemplateCreate(
            template_uuid="FULL-001",
            template_id=200,
            type="ARCA",
            name="full-template",
            description="A complete template",
            operator="admin",
            config=ArcaTemplateConfig(
                type="ARCA",
                base_url="https://arca.full",
                api_key="secret",
                arca_template_id="TPL-001",
                default_ttl_minutes=720,
                timeout=60.0,
            ),
        )
        expected = _make_template_response(
            template_uuid="FULL-001", name="full-template"
        )
        mock_svc.create_template.return_value = expected

        result = await create_template(
            request=request,
            tenant="prod_tenant",
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        assert result.data.name == "full-template"
        mock_svc.create_template.assert_called_once_with(
            tenant="prod_tenant", data=request
        )


# ==================== PUT update_template ====================


class TestUpdateTemplate:
    """Tests for update_template endpoint."""

    @pytest.mark.asyncio
    async def test_update_template_success(self) -> None:
        """Test updating a template."""
        mock_svc = MagicMock()
        request = _make_update_request()
        expected = _make_template_response(name="updated-name")
        mock_svc.update_template.return_value = expected

        result = await update_template(
            template_uuid="UUID-12345",
            request=request,
            tenant="test_tenant",
            status=TemplateStatus.ONLINE,
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        assert result.data == expected
        mock_svc.update_template.assert_called_once_with(
            tenant="test_tenant",
            template_uuid="UUID-12345",
            status=TemplateStatus.ONLINE,
            data=request,
        )

    @pytest.mark.asyncio
    async def test_update_template_default_status(self) -> None:
        """Test update with default status (ONLINE)."""
        mock_svc = MagicMock()
        request = _make_update_request()
        expected = _make_template_response()
        mock_svc.update_template.return_value = expected

        result = await update_template(
            template_uuid="UUID-12345",
            request=request,
            tenant="test_tenant",
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        mock_svc.update_template.assert_called_once_with(
            tenant="test_tenant",
            template_uuid="UUID-12345",
            status=TemplateStatus.ONLINE,
            data=request,
        )

    @pytest.mark.asyncio
    async def test_update_template_not_found(self) -> None:
        """Test update_template returns 404 when template not found."""
        mock_svc = MagicMock()
        mock_svc.update_template.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await update_template(
                template_uuid="MISSING",
                request=_make_update_request(),
                tenant="test_tenant",
                op_ctx=_OP_CTX,
                service=mock_svc,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TEMPLATE_NOT_FOUND"
        assert "MISSING" in exc_info.value.detail["message"]

    @pytest.mark.asyncio
    async def test_update_template_offline_status(self) -> None:
        """Test updating an OFFLINE template."""
        mock_svc = MagicMock()
        request = _make_update_request()
        expected = _make_template_response(status="OFFLINE")
        mock_svc.update_template.return_value = expected

        result = await update_template(
            template_uuid="UUID-OFF",
            request=request,
            tenant="test_tenant",
            status=TemplateStatus.OFFLINE,
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        mock_svc.update_template.assert_called_once_with(
            tenant="test_tenant",
            template_uuid="UUID-OFF",
            status=TemplateStatus.OFFLINE,
            data=request,
        )


# ==================== POST status-transitions ====================


class TestTransitionTemplateStatus:
    """Tests for transition_template_status endpoint."""

    @pytest.mark.asyncio
    async def test_transition_success(self) -> None:
        """Test transitioning CREATED -> AUDITED."""
        mock_svc = MagicMock()
        request = StatusTransitionRequest(
            current_status=TemplateStatus.CREATED,
            new_status=TemplateStatus.AUDITED,
        )
        expected = _make_template_response(status="AUDITED")
        mock_svc.update_status.return_value = expected

        result = await transition_template_status(
            template_uuid="UUID-12345",
            request=request,
            tenant="test_tenant",
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        assert result.data == expected
        mock_svc.update_status.assert_called_once_with(
            tenant="test_tenant",
            template_uuid="UUID-12345",
            current_status=TemplateStatus.CREATED,
            new_status=TemplateStatus.AUDITED,
        )

    @pytest.mark.asyncio
    async def test_transition_online_to_offline(self) -> None:
        """Test transitioning ONLINE -> OFFLINE."""
        mock_svc = MagicMock()
        request = StatusTransitionRequest(
            current_status=TemplateStatus.ONLINE,
            new_status=TemplateStatus.OFFLINE,
        )
        expected = _make_template_response(status="OFFLINE")
        mock_svc.update_status.return_value = expected

        result = await transition_template_status(
            template_uuid="UUID-12345",
            request=request,
            tenant="test_tenant",
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        assert result.data.status == "OFFLINE"
        mock_svc.update_status.assert_called_once_with(
            tenant="test_tenant",
            template_uuid="UUID-12345",
            current_status=TemplateStatus.ONLINE,
            new_status=TemplateStatus.OFFLINE,
        )

    @pytest.mark.asyncio
    async def test_transition_not_found(self) -> None:
        """Test status transition returns 404 when template not found."""
        mock_svc = MagicMock()
        mock_svc.update_status.return_value = None
        request = StatusTransitionRequest(
            current_status=TemplateStatus.CREATED,
            new_status=TemplateStatus.AUDITED,
        )

        with pytest.raises(HTTPException) as exc_info:
            await transition_template_status(
                template_uuid="MISSING",
                request=request,
                tenant="test_tenant",
                op_ctx=_OP_CTX,
                service=mock_svc,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TEMPLATE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_transition_audited_to_online(self) -> None:
        """Test transitioning AUDITED -> ONLINE."""
        mock_svc = MagicMock()
        request = StatusTransitionRequest(
            current_status=TemplateStatus.AUDITED,
            new_status=TemplateStatus.ONLINE,
        )
        expected = _make_template_response(status="ONLINE")
        mock_svc.update_status.return_value = expected

        result = await transition_template_status(
            template_uuid="UUID-12345",
            request=request,
            tenant="test_tenant",
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        mock_svc.update_status.assert_called_once_with(
            tenant="test_tenant",
            template_uuid="UUID-12345",
            current_status=TemplateStatus.AUDITED,
            new_status=TemplateStatus.ONLINE,
        )


# ==================== POST delete_template ====================


class TestDeleteTemplate:
    """Tests for delete_template endpoint."""

    @pytest.mark.asyncio
    async def test_delete_success(self) -> None:
        """Test deleting a template successfully."""
        mock_svc = MagicMock()
        mock_svc.soft_delete_template.return_value = True

        result = await delete_template(
            template_uuid="UUID-12345",
            tenant="test_tenant",
            status=TemplateStatus.ONLINE,
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        assert result.data is not None
        assert result.data.success is True
        assert result.data.message == "Template deleted"
        mock_svc.soft_delete_template.assert_called_once_with(
            tenant="test_tenant",
            template_uuid="UUID-12345",
            status=TemplateStatus.ONLINE,
            operator="test_user",
        )

    @pytest.mark.asyncio
    async def test_delete_not_found(self) -> None:
        """Test delete_template returns 404 when not found."""
        mock_svc = MagicMock()
        mock_svc.soft_delete_template.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await delete_template(
                template_uuid="MISSING",
                tenant="test_tenant",
                status=TemplateStatus.OFFLINE,
                op_ctx=_OP_CTX,
                service=mock_svc,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "TEMPLATE_NOT_FOUND"
        assert "MISSING" in exc_info.value.detail["message"]

    @pytest.mark.asyncio
    async def test_delete_offline_template(self) -> None:
        """Test deleting an OFFLINE template."""
        mock_svc = MagicMock()
        mock_svc.soft_delete_template.return_value = True

        result = await delete_template(
            template_uuid="UUID-OFF",
            tenant="test_tenant",
            status=TemplateStatus.OFFLINE,
            op_ctx=_OP_CTX,
            service=mock_svc,
        )

        assert result.code == 0
        mock_svc.soft_delete_template.assert_called_once_with(
            tenant="test_tenant",
            template_uuid="UUID-OFF",
            status=TemplateStatus.OFFLINE,
            operator="test_user",
        )
