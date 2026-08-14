"""E2E tests for device API endpoint.

Endpoints:
- GET /api/v1/devices/{device_uuid} - Get device info by UUID
"""

import pytest

pytestmark = [pytest.mark.e2e_asgi]


class TestDeviceApi:
    """Test suite for device info endpoints."""

    @pytest.mark.asyncio
    async def test_get_device_not_found(self, api, unique_id: str):
        """Test getting a non-existent device returns 404."""
        device_uuid = f"DEVICE-nonexistent-{unique_id}"
        resp = await api.client.get(f"/api/v1/devices/{device_uuid}")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "DEVICE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_get_device_invalid_uuid_format(self, api):
        """Test getting a device with garbage UUID returns 404."""
        resp = await api.client.get("/api/v1/devices/not-a-valid-device-uuid")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_device_by_bot_device_uuid(self, api, created_bot):
        """Test getting device info using the device UUID from a created bot."""
        bot_uuid = created_bot["bot_uuid"]

        # Fetch devices via the bot devices endpoint (bot is already activated)
        devices_resp = await api.client.get(
            api.bot_devices_url(bot_uuid),
            params=api.params(),
        )
        assert devices_resp.status_code == 200, (
            f"Bot devices failed: {devices_resp.status_code}"
        )
        data = devices_resp.json()["data"]
        # Response is ApiResponse[list[DeviceListResponse]]
        device_lists = data if isinstance(data, list) else [data]
        items = device_lists[0]["items"]
        assert len(items) > 0, "Created bot has no devices"

        device_uuid = items[0]["device_uuid"]

        resp = await api.client.get(api.device_url(device_uuid))
        assert resp.status_code == 200
        device = resp.json()["data"]
        assert device["device_uuid"] == device_uuid
