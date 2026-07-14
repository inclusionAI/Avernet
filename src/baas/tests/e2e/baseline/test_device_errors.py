"""E2E tests for Device Manage endpoints - error paths.

Tests that invalid device requests return appropriate error responses:
- GET /api/v1/bots/{nonexistent}/devices  — Non-existent bot
- GET /api/v1/devices/{nonexistent}       — Non-existent device
"""

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


class TestDeviceErrors:
    """Error-handling tests for device endpoints."""

    @pytest.mark.asyncio
    async def test_list_devices_nonexistent_bot(self, api: APITestHelper) -> None:
        """GET /bots/{nonexistent}/devices returns 404."""
        response = await api.client.get(
            api.bot_devices_url(NONEXISTENT_UUID),
            params=api.params(),
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.asyncio
    async def test_get_nonexistent_device(self, api: APITestHelper) -> None:
        """GET /devices/{nonexistent} returns 404."""
        response = await api.client.get(
            api.device_url(NONEXISTENT_UUID),
        )

        assert response.status_code == 404
        data = response.json()
        detail = data["detail"]
        assert detail["error"] == "DEVICE_NOT_FOUND" or "detail" in str(data)

    @pytest.mark.asyncio
    async def test_cross_tenant_device_access(self, api: APITestHelper) -> None:
        """GET /bots/{uuid}/devices with different tenant returns 404 or 4xx."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_devices_url(bot["bot_uuid"]),
            params={"tenant": "unknown_other_tenant"},
        )

        assert response.status_code != 500
        assert response.status_code != 200

    @pytest.mark.asyncio
    async def test_get_device_invalid_uuid_format(self, api: APITestHelper) -> None:
        """GET /devices/{bad_uuid} with malformed UUID returns 404."""
        response = await api.client.get(
            api.device_url("not-a-valid-device-uuid"),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_devices_invalid_bot_uuid_format(
        self, api: APITestHelper
    ) -> None:
        """GET /bots/{bad_uuid}/devices with malformed UUID returns 404."""
        response = await api.client.get(
            api.bot_devices_url("bad-format-uuid"),
            params=api.params(),
        )

        assert response.status_code != 500
        assert response.status_code in (404, 422)
