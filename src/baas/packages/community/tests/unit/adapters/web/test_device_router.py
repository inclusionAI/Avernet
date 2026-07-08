"""Tests for device_router.

Tests the GET /api/v1/devices/{device_uuid} endpoint:
- Device found → returns ApiResponse[DeviceResponse]
- Device not found → raises HTTPException 404
- Unexpected error → raises HTTPException 500
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from secbaas.adapters.web.routers.paas_service.device_router import (
    DeviceNotFoundResponse,
    get_device_info,
    router,
)
from secbaas.api import ApiResponse
from secbaas.api.device_manage import DeviceResponse

# ==================== Helpers ====================


def _make_device_response(
    id: int = 1,
    device_uuid: str = "DEVICE-abc123",
    tenant: str = "test_tenant",
    status: str = "ACTIVE",
) -> DeviceResponse:
    """Build a DeviceResponse for test assertions."""
    now = datetime.now(tz=UTC)
    return DeviceResponse(
        id=id,
        device_uuid=device_uuid,
        tenant=tenant,
        env="dev",
        domain="default",
        status=status,
        provider_type="ARCA",
        provider_device_id="sandbox-123",
        provider_device_props={"sandbox_id": "sb-001"},
        extra_config=None,
        err_msg=None,
        creator="user1",
        modifier="user1",
        gmt_create=now,
        gmt_modified=now,
    )


# ==================== Router Tests ====================


class TestRouterDefinition:
    """Tests for the APIRouter definition."""

    def test_router_prefix(self) -> None:
        """Test router has correct prefix."""
        assert router.prefix == "/api/v1/devices"

    def test_router_tags(self) -> None:
        """Test router has correct tags."""
        assert "设备管理" in router.tags

    def test_router_has_get_device_info_route(self) -> None:
        """Test router includes the get_device_info route."""
        route_paths = [r.path for r in router.routes]
        assert "/api/v1/devices/{device_uuid}" in route_paths


# ==================== Model Tests ====================


class TestDeviceNotFoundResponse:
    """Tests for DeviceNotFoundResponse model."""

    def test_model_creation(self) -> None:
        """Test DeviceNotFoundResponse can be created."""
        response = DeviceNotFoundResponse(
            error="DEVICE_NOT_FOUND",
            message="Device not found: DEVICE-xxx",
            device_uuid="DEVICE-xxx",
        )
        assert response.error == "DEVICE_NOT_FOUND"
        assert response.message == "Device not found: DEVICE-xxx"
        assert response.device_uuid == "DEVICE-xxx"

    def test_model_device_uuid_none(self) -> None:
        """Test DeviceNotFoundResponse with device_uuid=None."""
        response = DeviceNotFoundResponse(
            error="INTERNAL_ERROR",
            message="Something went wrong",
        )
        assert response.device_uuid is None

    def test_model_to_dict(self) -> None:
        """Test DeviceNotFoundResponse can be serialized to dict."""
        response = DeviceNotFoundResponse(
            error="DEVICE_NOT_FOUND",
            message="Device not found: DEVICE-abc",
            device_uuid="DEVICE-abc",
        )
        d = response.model_dump()
        assert d["error"] == "DEVICE_NOT_FOUND"
        assert d["message"] == "Device not found: DEVICE-abc"
        assert d["device_uuid"] == "DEVICE-abc"


# ==================== GET get_device_info ====================


class TestGetDeviceInfo:
    """Tests for get_device_info endpoint."""

    @pytest.mark.asyncio
    async def test_device_found_returns_200(self) -> None:
        """Test that a found device returns ApiResponse with DeviceResponse data."""
        expected = _make_device_response(device_uuid="DEVICE-abc123")
        mock_svc = MagicMock()
        mock_svc.get_device_info.return_value = expected

        result = await get_device_info(
            device_uuid="DEVICE-abc123", device_service=mock_svc
        )

        assert isinstance(result, ApiResponse)
        assert result.code == 0
        assert result.data is not None
        assert result.data.device_uuid == "DEVICE-abc123"
        assert result.data.status == "ACTIVE"
        assert result.data.tenant == "test_tenant"
        assert result.data.provider_type == "ARCA"
        mock_svc.get_device_info.assert_called_once_with(device_uuid="DEVICE-abc123")

    @pytest.mark.asyncio
    async def test_device_not_found_raises_404(self) -> None:
        """Test that a missing device raises HTTPException 404."""
        mock_svc = MagicMock()
        mock_svc.get_device_info.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_device_info(device_uuid="DEVICE-missing", device_service=mock_svc)

        assert exc_info.value.status_code == 404
        detail = exc_info.value.detail
        assert detail["error"] == "DEVICE_NOT_FOUND"
        assert "DEVICE-missing" in detail["message"]
        assert detail["device_uuid"] == "DEVICE-missing"

    @pytest.mark.asyncio
    async def test_deleted_device_raises_404(self) -> None:
        """Test that a soft-deleted device raises HTTPException 404."""
        mock_svc = MagicMock()
        mock_svc.get_device_info.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_device_info(device_uuid="DEVICE-deleted", device_service=mock_svc)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "DEVICE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_500(self) -> None:
        """Test that unexpected errors raise HTTPException 500."""
        mock_svc = MagicMock()
        mock_svc.get_device_info.side_effect = RuntimeError("Database connection lost")

        with pytest.raises(HTTPException) as exc_info:
            await get_device_info(device_uuid="DEVICE-abc123", device_service=mock_svc)

        assert exc_info.value.status_code == 500
        detail = exc_info.value.detail
        assert detail["error"] == "INTERNAL_ERROR"
        assert "Database connection lost" in detail["message"]
        assert detail["device_uuid"] == "DEVICE-abc123"

    @pytest.mark.asyncio
    async def test_device_with_all_fields(self) -> None:
        """Test that a device with all fields populated returns correctly."""
        now = datetime.now(tz=UTC)
        expected = DeviceResponse(
            id=42,
            device_uuid="DEVICE-full-001",
            tenant="prod_tenant",
            env="production",
            domain="api",
            status="ACTIVE",
            provider_type="ARCA",
            provider_device_id="sandbox-full",
            provider_device_props={"sandbox_id": "sb-full", "region": "cn-shanghai"},
            extra_config=None,
            err_msg=None,
            creator="admin",
            modifier="admin",
            gmt_create=now,
            gmt_modified=now,
        )
        mock_svc = MagicMock()
        mock_svc.get_device_info.return_value = expected

        result = await get_device_info(
            device_uuid="DEVICE-full-001", device_service=mock_svc
        )

        assert result.code == 0
        assert result.data is not None
        assert result.data.id == 42
        assert result.data.device_uuid == "DEVICE-full-001"
        assert result.data.tenant == "prod_tenant"
        assert result.data.env == "production"
        assert result.data.domain == "api"
        assert result.data.provider_device_id == "sandbox-full"
        assert result.data.provider_device_props == {
            "sandbox_id": "sb-full",
            "region": "cn-shanghai",
        }

    @pytest.mark.asyncio
    async def test_device_failed_status(self) -> None:
        """Test that a device with FAILED status returns correctly."""
        expected = _make_device_response(
            device_uuid="DEVICE-failed",
            status="FAILED",
        )
        mock_svc = MagicMock()
        mock_svc.get_device_info.return_value = expected

        result = await get_device_info(
            device_uuid="DEVICE-failed", device_service=mock_svc
        )

        assert result.code == 0
        assert result.data is not None
        assert result.data.status == "FAILED"
        assert result.data.device_uuid == "DEVICE-failed"

    @pytest.mark.asyncio
    async def test_device_pending_status(self) -> None:
        """Test that a device with PENDING status returns correctly."""
        expected = _make_device_response(
            device_uuid="DEVICE-pending",
            status="PENDING",
        )
        mock_svc = MagicMock()
        mock_svc.get_device_info.return_value = expected

        result = await get_device_info(
            device_uuid="DEVICE-pending", device_service=mock_svc
        )

        assert result.code == 0
        assert result.data is not None
        assert result.data.status == "PENDING"

    @pytest.mark.asyncio
    async def test_device_special_characters_uuid(
        self,
    ) -> None:
        """Test that UUID with hyphens and alphanumeric chars works."""
        expected = _make_device_response(device_uuid="DEVICE-a1b2c3d4-e5f6-7890")
        mock_svc = MagicMock()
        mock_svc.get_device_info.return_value = expected

        result = await get_device_info(
            device_uuid="DEVICE-a1b2c3d4-e5f6-7890", device_service=mock_svc
        )

        assert result.code == 0
        assert result.data is not None
        assert result.data.device_uuid == "DEVICE-a1b2c3d4-e5f6-7890"

    @pytest.mark.asyncio
    async def test_http_exception_re_raised_as_is(
        self,
    ) -> None:
        """Test that HTTPException raised by service is re-raised directly."""
        mock_svc = MagicMock()
        mock_svc.get_device_info.side_effect = HTTPException(
            status_code=429,
            detail={"error": "RATE_LIMITED", "message": "Too many requests"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_device_info(device_uuid="DEVICE-abc123", device_service=mock_svc)

        assert exc_info.value.status_code == 429
        assert exc_info.value.detail["error"] == "RATE_LIMITED"

    @pytest.mark.asyncio
    async def test_unexpected_error_with_empty_message(
        self,
    ) -> None:
        """Test unexpected error with empty message still returns 500."""
        mock_svc = MagicMock()
        mock_svc.get_device_info.side_effect = Exception("")

        with pytest.raises(HTTPException) as exc_info:
            await get_device_info(device_uuid="DEVICE-abc123", device_service=mock_svc)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail["error"] == "INTERNAL_ERROR"
        assert exc_info.value.detail["device_uuid"] == "DEVICE-abc123"

    @pytest.mark.asyncio
    async def test_device_without_provider_info(self) -> None:
        """Test device with no provider info (legacy/no PaaS attached)."""
        expected = _make_device_response(device_uuid="DEVICE-no-provider")
        expected.provider_type = None
        expected.provider_device_id = None
        expected.provider_device_props = None
        mock_svc = MagicMock()
        mock_svc.get_device_info.return_value = expected

        result = await get_device_info(
            device_uuid="DEVICE-no-provider", device_service=mock_svc
        )

        assert result.code == 0
        assert result.data is not None
        assert result.data.provider_type is None
        assert result.data.provider_device_id is None
        assert result.data.provider_device_props is None
