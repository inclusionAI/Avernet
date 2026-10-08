"""E2E tests for Bot Runtime start / lifecycle detail endpoints.

Tests that bots with ACTIVE status expose the expected lifecycle
and detail endpoints:
- GET  /api/v1/bots/{bot_uuid}/detail-by-uuid  — Bot detail
- GET  /api/v1/bots/{bot_uuid}/devices           — Device list
"""

from typing import Any

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


class TestBotStart:
    """Bot detail and start-progress tests."""

    pytestmark = pytest.mark.start

    @pytest.mark.asyncio
    async def test_get_bot_detail(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        """GET /bots/{bot_uuid}/detail-by-uuid returns bot detail records."""
        bot = created_bot

        response = await api.client.get(
            api.bot_detail_url(bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "items" in data["data"]
        for item in data["data"]["items"]:
            assert "bot_uuid" in item

    @pytest.mark.asyncio
    async def test_get_bot_not_found(self, api: APITestHelper) -> None:
        """GET /bots/{nonexistent}/detail-by-uuid returns 404."""
        response = await api.client.get(
            api.bot_detail_url(NONEXISTENT_UUID),
            params=api.params(),
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error_code"] == "BOT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_bot_devices_list(
        self, api: APITestHelper, created_bot: dict[str, Any]
    ) -> None:
        """GET /bots/{bot_uuid}/devices returns device list."""
        bot = created_bot

        response = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/devices",
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert isinstance(data["data"], list)
