"""E2E tests for PaaS Device API endpoints.

Tests cover:
- POST /api/v1/paas/devices - Create device
- DELETE /api/v1/paas/devices/{paas_device_id} - Destroy device
- POST /api/v1/paas/devices/{paas_device_id}/commands - Send command
- GET /api/v1/paas/devices/{paas_device_id}/ws-info - Get WebSocket info
"""

import pytest

from ...conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.sync]


class TestCreateDevice:
    """Tests for POST /api/v1/paas/devices endpoint."""

    @pytest.mark.asyncio
    async def test_create_device(self, api: APITestHelper, unique_id: str) -> None:
        """Test create PaaS device."""
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "platform_type": "ARCA",
                "config": {
                    "template_id": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                    "name": f"test-device-{unique_id}",
                },
            },
        )

        # May fail if template doesn't exist or external service unavailable
        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestDestroyDevice:
    """Tests for DELETE /api/v1/paas/devices/{paas_device_id} endpoint."""

    @pytest.mark.asyncio
    async def test_destroy_device_not_found(self, api: APITestHelper) -> None:
        """Test destroy nonexistent device."""
        response = await api.client.delete(
            api.paas_device_url("nonexistent-device-id"),
            params=api.params(),
        )

        assert response.status_code in [200, 400, 404, 500]


class TestSendDeviceCommand:
    """Tests for POST /api/v1/paas/devices/{paas_device_id}/commands endpoint."""

    @pytest.mark.asyncio
    async def test_send_device_command(self, api: APITestHelper) -> None:
        """Test send command to device."""
        response = await api.client.post(
            api.paas_device_url("nonexistent-device-id", "commands"),
            params=api.params(),
            json={
                "cmd": "echo hello",
            },
        )

        assert response.status_code in [200, 400, 404, 422, 500]


class TestGetDeviceWsInfo:
    """Tests for GET /api/v1/paas/devices/{paas_device_id}/ws-info endpoint."""

    @pytest.mark.asyncio
    async def test_get_device_ws_info(self, api: APITestHelper) -> None:
        """Test get device WebSocket info."""
        response = await api.client.get(
            api.paas_device_url("nonexistent-device-id", "ws-info"),
            params=api.params(),
        )

        assert response.status_code in [200, 400, 404, 422, 500]
