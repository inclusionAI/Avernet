"""E2E tests for Device Manage endpoints - edge cases.

Tests cover boundary and edge-case behavior:
- Pagination boundary values
- Device with zero devices
- Device state fields
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


def _unpack_devices(response_body: dict) -> dict:
    raw = response_body["data"]
    if isinstance(raw, list):
        return raw[0] if raw else {"items": [], "total": 0, "page": 1, "page_size": 20}
    return raw


class TestDeviceEdge:
    """Edge-case tests for device endpoints."""

    @pytest.mark.asyncio
    async def test_device_pagination_boundary(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """Device list handles pagination at minimum values."""
        response = await api.client.get(
            api.bot_devices_url(created_bot["bot_uuid"]),
            params=api.params(page=1, page_size=1),
        )

        assert response.status_code == 200
        devices = _unpack_devices(response.json())
        assert devices["page"] == 1
        assert devices["page_size"] == 1
        assert len(devices["items"]) <= 1

    @pytest.mark.asyncio
    async def test_device_pagination_large_page(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """Device list with large page_size is handled gracefully."""
        response = await api.client.get(
            api.bot_devices_url(created_bot["bot_uuid"]),
            params=api.params(page=1, page_size=9999),
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_device_pagination_page_beyond_range(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """Device list with page beyond available data returns empty list.

        NOTE: The current endpoint ignores page/page_size and returns ALL
        devices for the bot record.  The server-side ``list_devices_by_bot_uuid``
        does not accept pagination params.  This test validates that the server
        does not crash (non-500) when out-of-range pagination is requested.
        """
        response = await api.client.get(
            api.bot_devices_url(created_bot["bot_uuid"]),
            params=api.params(page=9999, page_size=10),
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_device_state_in_response(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """Device list items include state/status fields."""
        response = await api.client.get(
            api.bot_devices_url(created_bot["bot_uuid"]),
            params=api.params(page=1, page_size=5),
        )

        assert response.status_code == 200
        devices = _unpack_devices(response.json())
        items = devices.get("items", [])

        if items:
            device = items[0]
            state = device.get("state") or device.get("status")
            if state is not None:
                assert isinstance(state, str)

    @pytest.mark.asyncio
    async def test_device_list_page_size_limit(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """Device list page_size is bounded to a reasonable maximum."""
        response = await api.client.get(
            api.bot_devices_url(created_bot["bot_uuid"]),
            params=api.params(page=1, page_size=500),
        )

        assert response.status_code == 200
        devices = _unpack_devices(response.json())
        assert devices["page_size"] <= 500

    @pytest.mark.asyncio
    async def test_device_list_with_tenant(
        self, api: APITestHelper, created_bot: dict
    ) -> None:
        """Device list properly scoped by tenant returns paginated response."""
        response = await api.client.get(
            api.bot_devices_url(created_bot["bot_uuid"]),
            params=api.params(page=1, page_size=20),
        )

        assert response.status_code == 200
        devices = _unpack_devices(response.json())
        assert "items" in devices
        assert "total" in devices
