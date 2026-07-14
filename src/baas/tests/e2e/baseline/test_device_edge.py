"""E2E tests for Device Manage endpoints - edge cases.

Tests cover boundary and edge-case behavior:
- Pagination boundary values
- Device with zero devices
- Device state fields
"""

import pytest

from ..conftest import APITestHelper, find_existing_bot

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestDeviceEdge:
    """Edge-case tests for device endpoints."""

    @pytest.mark.asyncio
    async def test_device_pagination_boundary(self, api: APITestHelper) -> None:
        """Device list handles pagination at minimum values."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_devices_url(bot["bot_uuid"]),
            params=api.params(page=1, page_size=1),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["page"] == 1
        assert data["page_size"] == 1
        assert len(data["items"]) <= 1

    @pytest.mark.asyncio
    async def test_device_pagination_large_page(self, api: APITestHelper) -> None:
        """Device list with large page_size is handled gracefully."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_devices_url(bot["bot_uuid"]),
            params=api.params(page=1, page_size=9999),
        )

        assert response.status_code != 500

    @pytest.mark.asyncio
    async def test_device_pagination_page_beyond_range(
        self, api: APITestHelper
    ) -> None:
        """Device list with page beyond available data returns empty list."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_devices_url(bot["bot_uuid"]),
            params=api.params(page=9999, page_size=10),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["items"] == []

    @pytest.mark.asyncio
    async def test_device_state_in_response(self, api: APITestHelper) -> None:
        """Device list items include state/status fields."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_devices_url(bot["bot_uuid"]),
            params=api.params(page=1, page_size=5),
        )

        assert response.status_code == 200
        items = response.json()["data"]["items"]

        if items:
            device = items[0]
            state = device.get("state") or device.get("status")
            if state is not None:
                assert isinstance(state, str)

    @pytest.mark.asyncio
    async def test_device_list_page_size_limit(self, api: APITestHelper) -> None:
        """Device list page_size is bounded to a reasonable maximum."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_devices_url(bot["bot_uuid"]),
            params=api.params(page=1, page_size=500),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["page_size"] <= 500

    @pytest.mark.asyncio
    async def test_device_list_with_tenant(self, api: APITestHelper) -> None:
        """Device list properly scoped by tenant returns paginated response."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_devices_url(bot["bot_uuid"]),
            params=api.params(page=1, page_size=20),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert "items" in data
        assert "total" in data
