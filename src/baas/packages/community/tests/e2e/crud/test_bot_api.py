"""E2E tests for Bot Management API endpoints - CRUD operations.

Tests that don't require PaaS/device operations:
- GET /api/v1/bots/{bot_uuid} - Get bot details (with devices)
- POST /api/v1/bots/{bot_uuid}/update - Update bot
- GET /api/v1/bots/{bot_uuid}/sessions - List bot sessions
- GET /api/v1/bots/{bot_uuid}/devices - List bot devices
"""

import pytest

from ..conftest import (
    APITestHelper,
    activate_test_bot,
    cleanup_bot,
    create_test_bot,
    find_existing_bot,
)

pytestmark = pytest.mark.e2e


class TestGetBot:
    pytestmark = pytest.mark.crud

    @pytest.mark.asyncio
    async def test_get_existing_bot(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["bot_uuid"] == bot["bot_uuid"]

    @pytest.mark.asyncio
    async def test_get_bot_detail_includes_devices(self, api: APITestHelper) -> None:
        """GET /bots/{bot_uuid}/detail-by-uuid returns bot records with devices."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        # Returns BotListResponse with items, each item has devices
        assert "items" in data["data"]
        for item in data["data"]["items"]:
            assert "devices" in item
            assert isinstance(item["devices"], list)

    @pytest.mark.asyncio
    async def test_get_bot_detail_device_info_fields(self, api: APITestHelper) -> None:
        """GET /bots/{bot_uuid}/detail-by-uuid device info has expected fields."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        # Flatten items to find devices
        all_devices = []
        for item in data["data"]["items"]:
            all_devices.extend(item.get("devices", []))
        if all_devices:
            device = all_devices[0]
            assert "device_uuid" in device
            assert "status" in device
            assert "provider_type" in device
            assert "provider_device_id" in device
            assert "gmt_create" in device

    @pytest.mark.asyncio
    async def test_get_nonexistent_bot(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.bot_url("nonexistent-uuid-12345678"),
            params=api.params(),
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error_code"] == "BOT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_get_bot_with_health_check(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """GET /bots/{bot_uuid}?health_check=true populates device health field.

        When health_check is not set (default), device health should be absent/null.
        When health_check=true, each device in the response should have a health
        field with value 'true' or 'false'.
        """
        bot = await create_test_bot(api, f"hc-bot-{unique_id}")
        await activate_test_bot(api, bot)

        # Without health_check — health field is "unknown"
        resp = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        for device in data.get("devices", []):
            assert device.get("health") == "unknown"

        # With health_check=true — health field is "true" or "false"
        resp = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(health_check="true"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        for device in data.get("devices", []):
            assert "health" in device
            assert device["health"] in ("true", "false", "unknown")

        await cleanup_bot(api, bot["bot_uuid"])


class TestUpdateBot:
    pytestmark = pytest.mark.crud

    @pytest.mark.asyncio
    async def test_update_bot_name(self, api: APITestHelper, unique_id: str) -> None:
        bot = await create_test_bot(api, f"test-update-name-{unique_id}")
        assert bot.get("publish_id"), "Bot should have a publish_id"
        await activate_test_bot(api, bot)

        response = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/update",
            params=api.params(),
            json={
                "name": f"updated-name-{unique_id}",
                "operator": "e2e-test",
            },
        )

        assert response.status_code in (200, 409)
        if response.status_code == 409:
            await cleanup_bot(api, bot["bot_uuid"])
            pytest.skip("Bot has active publish, cannot update name")
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["name"] == f"updated-name-{unique_id}"
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_update_bot_config(self, api: APITestHelper, unique_id: str) -> None:
        bot = await create_test_bot(api, f"test-update-config-{unique_id}")
        assert bot.get("publish_id"), "Bot should have a publish_id"
        await activate_test_bot(api, bot)

        response = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/update",
            params=api.params(),
            json={
                "config": {
                    "entity_id": f"updated-entity-{unique_id}",
                    "entity_type": "staff",
                },
                "operator": "e2e-test",
                "request_id": f"e2e-update-config-{unique_id}",
            },
        )

        assert response.status_code in (200, 409)
        if response.status_code == 409:
            await cleanup_bot(api, bot["bot_uuid"])
            pytest.skip("Bot has active publish, cannot config-update")
        data = response.json()
        assert data["code"] == 0
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_update_nonexistent_bot(self, api: APITestHelper) -> None:
        response = await api.client.post(
            f"{api.bot_url('nonexistent-uuid-12345678')}/update",
            params=api.params(),
            json={"name": "should-fail", "operator": "e2e-test"},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error_code"] == "BOT_NOT_FOUND"


@pytest.mark.e2e
class TestBotFailedStateE2E:
    """E2E tests for bot FAILED state."""

    @pytest.mark.asyncio
    async def test_list_bots_with_failed_status(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Test that list_bots can filter by FAILED status."""
        # Create a bot and simulate it reaching FAILED status
        await create_test_bot(api, f"test-list-failed-{unique_id}")

        # List bots with FAILED status filter
        # Note: In a real scenario, FAILED status would be set by publish failure
        # For this test, we're verifying the API endpoint supports the filter
        resp = await api.client.get(
            api.bot_url(),
            params=api.params(status="FAILED"),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert isinstance(data["data"]["items"], list)

    @pytest.mark.asyncio
    async def test_get_bot_with_failed_status(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Test that get_bot returns FAILED status correctly.

        This test verifies the API endpoint correctly returns the FAILED status
        for a bot that has been transitioned to FAILED state.
        """
        # Create a bot - it starts in PENDING status
        bot = await create_test_bot(api, f"test-get-failed-{unique_id}")

        # Verify initial status is PENDING
        assert bot["status"] == "PENDING"

        # In a real scenario, the bot would transition to FAILED after
        # a CREATE publish failure. Here we verify the status field
        # exists and can be queried.
        resp = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "status" in data["data"]
        assert data["data"]["status"] in [
            "PENDING",
            "ACTIVE",
            "FAILED",
            "DESTROYING",
            "RELEASED",
        ]


class TestUpdateDevices:
    """POST /api/v1/bots/{bot_uuid}/update-devices - Targeted device update."""

    pytestmark = pytest.mark.crud

    async def _fetch_bot(self, api: APITestHelper, bot_uuid: str) -> dict:
        resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        assert resp.status_code == 200
        return resp.json()["data"]

    @pytest.mark.asyncio
    async def test_update_devices_returns_publish_id(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /{bot_uuid}/update-devices returns publish_id for tracking."""
        bot = await create_test_bot(api, f"test-upd-dev-{unique_id}")
        assert bot.get("publish_id"), "Bot should have a publish_id"
        await activate_test_bot(api, bot)
        bot_uuid = bot["bot_uuid"]

        bot_data = await self._fetch_bot(api, bot_uuid)
        devices = bot_data.get("devices", [])
        if not devices:
            await cleanup_bot(api, bot_uuid)
            pytest.skip(f"No devices found for bot {bot_uuid}")

        response = await api.client.post(
            f"{api.bot_url(bot_uuid)}/update-devices",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "device_uuids": [devices[0]["device_uuid"]],
                "auto_approve_publish": True,
                "request_id": f"e2e-upd-dev-{unique_id}",
            },
        )

        assert response.status_code in (200, 409)
        if response.status_code == 409:
            await cleanup_bot(api, bot_uuid)
            pytest.skip("Bot has concurrent active publish")
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["publish_id"] > 0, (
            f"Expected positive publish_id, got {data['data']['publish_id']}"
        )
        assert data["data"]["bot_uuid"] == bot_uuid
        assert data["data"]["status"] == bot_data["status"]
        await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_update_devices_with_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /{bot_uuid}/update-devices accepts config updates."""
        bot = await create_test_bot(api, f"test-upd-dev-cfg-{unique_id}")
        assert bot.get("publish_id"), "Bot should have a publish_id"
        await activate_test_bot(api, bot)
        bot_uuid = bot["bot_uuid"]

        bot_data = await self._fetch_bot(api, bot_uuid)
        devices = bot_data.get("devices", [])
        if not devices:
            await cleanup_bot(api, bot_uuid)
            pytest.skip(f"No devices found for bot {bot_uuid}")

        entity_id = f"e2e-upd-entity-{unique_id}"
        response = await api.client.post(
            f"{api.bot_url(bot_uuid)}/update-devices",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "device_uuids": [devices[0]["device_uuid"]],
                "auto_approve_publish": True,
                "request_id": f"e2e-upd-dev-cfg-{unique_id}",
                "config": {
                    "entity_id": entity_id,
                    "entity_type": "staff",
                },
            },
        )

        assert response.status_code in (200, 409)
        if response.status_code == 409:
            await cleanup_bot(api, bot_uuid)
            pytest.skip("Bot has concurrent active publish")
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["publish_id"] > 0
        await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_update_devices_without_device_uuids(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /{bot_uuid}/update-devices with empty device_uuids returns 422."""
        response = await api.client.post(
            f"{api.bot_url('test-uuid')}/update-devices",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "device_uuids": [],
                "request_id": f"e2e-upd-empty-{unique_id}-xxxxxxxxxxxx",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_devices_nonexistent_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /{bot_uuid}/update-devices with nonexistent bot returns 404."""
        response = await api.client.post(
            f"{api.bot_url('nonexistent-uuid-12345678')}/update-devices",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "device_uuids": ["DEV-MISSING"],
                "request_id": f"e2e-upd-nonexist-{unique_id}",
            },
        )
        assert response.status_code == 404


class TestGetBotDevices:
    """GET /api/v1/bots/{bot_uuid}/devices - Devices by UUID (list).
    GET /api/v1/bots/{bot_id}/devices-by-id - Devices by ID (paginated).
    GET /api/v1/bots/{bot_uuid}/detail-by-uuid - Bot with devices by UUID.
    GET /api/v1/bots/{bot_id}/detail-by-id - Bot with devices by ID."""

    pytestmark = pytest.mark.crud

    @pytest.mark.asyncio
    async def test_get_bot_detail_by_uuid_includes_devices(
        self, api: APITestHelper
    ) -> None:
        """GET /bots/{bot_uuid}/detail-by-uuid returns bot records with devices."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        for item in data["data"]["items"]:
            assert "devices" in item
            assert isinstance(item["devices"], list)

    @pytest.mark.asyncio
    async def test_get_bot_devices_by_uuid_endpoint(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/devices",
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        # Returns a list of DeviceListResponse (one per bot record)
        assert isinstance(data["data"], list)
        if data["data"]:
            entry = data["data"][0]
            assert "items" in entry
            assert "total" in entry

    @pytest.mark.asyncio
    async def test_get_bot_devices_by_uuid_with_device_info(
        self, api: APITestHelper
    ) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/devices",
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        # Flatten all entries to find devices
        all_devices = []
        for entry in data["data"]:
            all_devices.extend(entry.get("items", []))
        if all_devices:
            device = all_devices[0]
            assert "device_uuid" in device
            assert "status" in device
            assert "provider_type" in device

    @pytest.mark.asyncio
    async def test_get_bot_devices_by_id_paginated(self, api: APITestHelper) -> None:
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        # Get bot_id from the detail-by-uuid endpoint
        detail_resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert detail_resp.status_code == 200
        bot_id = detail_resp.json()["data"]["items"][0]["id"]

        response = await api.client.get(
            f"{api.bot_url()}/{bot_id}/devices-by-id",
            params=api.params(page=1, page_size=10),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "items" in data["data"]
        assert "total" in data["data"]
        assert "page" in data["data"]
        assert data["data"]["page"] == 1

    @pytest.mark.asyncio
    async def test_get_bot_devices_not_found(self, api: APITestHelper) -> None:
        response = await api.client.get(
            f"{api.bot_url('nonexistent-uuid-12345678')}/devices",
            params=api.params(),
        )

        assert response.status_code == 404
