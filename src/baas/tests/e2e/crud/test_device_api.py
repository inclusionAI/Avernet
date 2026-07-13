"""E2E tests for device API endpoint.

Endpoints:
- GET /api/v1/devices/{device_uuid} - Get device info by UUID
"""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.crud]


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
    async def test_get_device_by_bot_device_uuid(self, api):
        """Test getting device info using the device UUID from an existing bot."""
        # First get an existing bot with devices
        resp = await api.client.get(
            api.bot_url(),
            params=api.params(page=1, page_size=1, status="ACTIVE"),
        )
        if resp.status_code != 200:
            pytest.skip("Cannot list bots")
        data = resp.json()["data"]
        if not data.get("items"):
            pytest.skip("No bots available for test")

        bot = data["items"][0]
        bot_uuid = bot.get("bot_uuid") or bot.get("uuid")
        if not bot_uuid:
            pytest.skip("Bot has no uuid")

        # Get bot devices
        devices_resp = await api.client.get(
            f"/api/v1/bots/{bot_uuid}/devices",
            params=api.params(),
        )
        if devices_resp.status_code != 200:
            pytest.skip("Cannot get bot devices")
        devices_data = devices_resp.json().get("data", {})
        items = devices_data.get("items", []) if isinstance(devices_data, dict) else []
        if not items:
            pytest.skip("Bot has no devices")

        device_uuid = (
            items[0].get("device_uuid") or items[0].get("uuid") or items[0].get("id")
        )
        if not device_uuid:
            pytest.skip("Device has no uuid")

        # Get device by UUID
        resp = await api.client.get(f"/api/v1/devices/{device_uuid}")
        assert resp.status_code == 200
        device = resp.json()["data"]
        assert device["device_uuid"] == device_uuid or device.get("uuid") == device_uuid
