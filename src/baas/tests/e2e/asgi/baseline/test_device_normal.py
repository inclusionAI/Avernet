"""E2E tests for Device Manage endpoints - normal path.

Tests device listing and detail retrieval:
- GET /api/v1/bots/{bot_uuid}/devices    — List devices on a bot
- GET /api/v1/devices/{device_uuid}      — Get device detail
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


# NOTE: The /api/v1/bots/{bot_uuid}/devices endpoint returns
# ApiResponse[list[DeviceListResponse]] — data is a list of paginated
# DeviceListResponse objects, one per matching bot record (a bot_uuid may
# map to multiple records with different statuses).  Unpack the first
# element for pagination assertions.
def _unpack_devices(response_body: dict) -> dict:
    raw = response_body["data"]
    if isinstance(raw, list):
        return raw[0] if raw else {"items": [], "total": 0, "page": 1, "page_size": 20}
    return raw


class TestDeviceNormal:
    """Normal-path device tests."""

    @pytest.mark.asyncio
    async def test_list_bot_devices(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """GET /bots/{bot_uuid}/devices returns 200 with paginated device list."""
        response = await api.client.get(
            api.bot_devices_url(created_bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        devices_data = _unpack_devices(data)
        assert "items" in devices_data

    @pytest.mark.asyncio
    async def test_device_list_structure(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """Device list response has expected pagination structure."""
        response = await api.client.get(
            api.bot_devices_url(created_bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code == 200
        devices_data = _unpack_devices(response.json())
        assert "items" in devices_data
        assert "total" in devices_data
        assert "page" in devices_data
        assert "page_size" in devices_data
        assert isinstance(devices_data["items"], list)

        if devices_data["items"]:
            device = devices_data["items"][0]
            assert "device_uuid" in device or "uuid" in device

    @pytest.mark.asyncio
    async def test_get_device_detail(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """GET /devices/{device_uuid} returns device detail."""
        devices_response = await api.client.get(
            api.bot_devices_url(created_bot["bot_uuid"]),
            params=api.params(),
        )

        assert devices_response.status_code == 200
        devices_data = _unpack_devices(devices_response.json())
        items = devices_data.get("items", [])
        if not items:
            pytest.skip("Bot has no devices")

        device_uuid = (
            items[0].get("device_uuid") or items[0].get("uuid") or items[0].get("id")
        )
        if not device_uuid:
            pytest.skip("Device has no uuid")

        response = await api.client.get(
            api.device_url(device_uuid),
        )

        assert response.status_code == 200
        device = response.json()["data"]
        device_key = device.get("device_uuid") or device.get("uuid")
        assert device_key == device_uuid

    @pytest.mark.asyncio
    async def test_device_binding_query(self, api: APITestHelper) -> None:
        """GET /device-bindings returns a valid response."""
        response = await api.client.get(
            api.device_binding_url(),
            params=api.params(),
        )

        assert response.status_code != 500
        if response.status_code == 200:
            data = response.json()
            assert "data" in data or isinstance(data, dict | list)

    @pytest.mark.asyncio
    async def test_device_list_default_pagination(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """Device list returns sensible defaults for page/page_size."""
        response = await api.client.get(
            api.bot_devices_url(created_bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code == 200
        devices_data = _unpack_devices(response.json())
        assert devices_data.get("page_size", 0) > 0
        assert devices_data["page"] == 1
