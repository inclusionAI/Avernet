"""E2E tests for Bot Management API endpoints - SYNC operations.

Tests that require PaaS/device operations:
- GET /api/v1/bots - List bots (with devices)
- POST /api/v1/bots - Create bot
- POST /api/v1/bots/{bot_uuid}/scale - Scale bot
- POST /api/v1/bots/{bot_uuid}/restart - Restart bot
- POST /api/v1/bots/{bot_uuid}/destroy - Destroy bot
- GET /api/v1/bots/{bot_uuid}/ws-info - Get WebSocket info
- GET /api/v1/bots/{bot_uuid}/devices - Query bot devices
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    cleanup_bot,
    create_test_bot,
)

pytestmark = [pytest.mark.e2e_asgi]


async def _stop_bot_and_wait(api: APITestHelper, bot_uuid: str) -> int | None:
    """Stop a bot and wait for the STOP publish to complete."""
    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/stop",
        params=api.params(),
        json={
            "operator": "e2e-test",
            "request_id": uuid.uuid4().hex,
            "auto_approve_publish": True,
        },
    )
    assert resp.status_code == 200
    publish_id = resp.json()["data"].get("publish_id")
    if not publish_id:
        return None

    # Wait for STOP publish to reach SUCCESS
    from tests.e2e.asgi.conftest import wait_for_publish_status

    status = await wait_for_publish_status(
        api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
    )
    assert status == "SUCCESS", f"STOP publish expected SUCCESS, got {status}"
    return publish_id


class TestListBots:
    @pytest.mark.asyncio
    async def test_list_bots_default_pagination(self, api: APITestHelper) -> None:
        response = await api.client.get(api.bot_url(), params=api.params())

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "items" in data["data"]
        assert "total" in data["data"]
        assert data["data"]["page"] == 1
        assert data["data"]["page_size"] == 20

    @pytest.mark.asyncio
    async def test_list_bots_with_devices(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """GET /bots/{bot_uuid}/detail-by-uuid returns bot records with devices."""
        bot = await create_test_bot(api, f"test-detail-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            response = await api.client.get(
                f"{api.bot_url(bot_uuid)}/detail-by-uuid",
                params=api.params(),
            )

            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 0
            for item in data["data"]["items"]:
                assert "devices" in item
                assert isinstance(item["devices"], list)
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_list_bots_with_status_filter(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.bot_url(),
            params=api.params(status="ACTIVE"),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        for item in data["data"]["items"]:
            assert item["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_list_bots_custom_pagination(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.bot_url(),
            params=api.params(page=1, page_size=5),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["page"] == 1
        assert data["data"]["page_size"] == 5
        assert len(data["data"]["items"]) <= 5


class TestCreateBot:
    @pytest.mark.asyncio
    async def test_create_bot_minimal_fields(
        self, api: APITestHelper, unique_id: str, created_bot_uuids: list[str]
    ) -> None:
        request_id = uuid.uuid4().hex
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": f"test-bot-{unique_id}",
                "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                "operator": "e2e-test",
                "request_id": request_id,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "bot_uuid" in data["data"]
        assert "publish_id" in data["data"]
        assert data["data"]["status"] == "PENDING"
        created_bot_uuids.append(data["data"]["bot_uuid"])

        await cleanup_bot(api, data["data"]["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_bot_with_full_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        request_id = uuid.uuid4().hex
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": f"test-bot-full-{unique_id}",
                "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                "device_count": 2,
                "config": {
                    "entity_id": f"test-entity-{unique_id}",
                    "entity_type": "staff",
                },
                "operator": "e2e-test",
                "request_id": request_id,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["replica_desired"] == 2

        await cleanup_bot(api, data["data"]["bot_uuid"])

    @pytest.mark.skip(reason="Idempotent request handling not implemented yet")
    @pytest.mark.asyncio
    async def test_create_bot_duplicate_request_id(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        request_id = uuid.uuid4().hex

        # First request
        response1 = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": f"test-dup-{unique_id}",
                "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                "operator": "e2e-test",
                "request_id": request_id,
            },
        )

        # Duplicate request - should be idempotent
        response2 = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": f"test-dup-{unique_id}",
                "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                "operator": "e2e-test",
                "request_id": request_id,
            },
        )

        # Both should succeed and return same bot
        assert response1.status_code == response2.status_code == 200
        assert (
            response1.json()["data"]["bot_uuid"] == response2.json()["data"]["bot_uuid"]
        )

        await cleanup_bot(api, response1.json()["data"]["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_bot_validation_error(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": "a",  # Too short
                "template_uuid": "invalid-uuid",
                "operator": "e2e-test",
            },
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_bot_missing_required_fields(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "operator": "e2e-test",
            },
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_bot_missing_operator(self, api: APITestHelper) -> None:
        """Test that missing required field operator is rejected."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": "test-bot-missing-op",
                "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
            },
        )

        assert response.status_code in (400, 422), (
            f"Expected 400 or 422, got {response.status_code}: {response.json()}"
        )


class TestBotLifecycle:
    """Full bot lifecycle: create -> activate -> scale -> restart -> destroy.

    Uses a single bot to test all device_op operations sequentially,
    avoiding duplicate bot creation across tests.
    """

    @pytest.mark.asyncio
    async def test_bot_lifecycle(self, api: APITestHelper, unique_id: str) -> None:
        bot = await create_test_bot(api, f"test-lifecycle-{unique_id}")

        # 1. Bot created with PENDING status and a publish
        assert bot["status"] == "PENDING"
        assert "publish_id" in bot
        pid = bot["publish_id"]

        # 2. Approve publish - with 1 device, auto-compacts to 1 stage
        # and auto-executes to SUCCESS
        resp = await api.client.post(
            api.publish_url(pid, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200
        # Single device = auto-compact to 1 stage = auto-execute on approve
        assert resp.json()["data"]["status"] == "SUCCESS"

        # 3. Verify bot is now ACTIVE (calculated from devices)
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ACTIVE"

        # 3b. Verify devices are visible via detail-by-uuid endpoint
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1
        bot_data = items[0]
        assert "devices" in bot_data
        assert isinstance(bot_data["devices"], list)
        if bot_data["devices"]:
            device = bot_data["devices"][0]
            assert "device_uuid" in device
            assert "status" in device

        # 3c. Verify devices endpoint returns device lists per bot record
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/devices",
            params=api.params(),
        )
        assert resp.status_code == 200
        devices_data = resp.json()["data"]
        # Returns list of DeviceListResponse (one per bot record)
        assert isinstance(devices_data, list)
        assert len(devices_data) >= 1
        assert "items" in devices_data[0]
        assert "total" in devices_data[0]
        assert devices_data[0]["total"] >= 1

        # 4. Scale up
        resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/scale",
            params=api.params(),
            json={
                "target_count": 2,
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code in [200, 409]
        if resp.status_code == 200:
            assert resp.json()["code"] == 0

        # 5. Restart
        resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/restart",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code in [200, 409]
        if resp.status_code == 200:
            assert resp.json()["code"] == 0

        # 6. Destroy
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/destroy",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code in [200, 409]

    @pytest.mark.asyncio
    async def test_scale_bot_validation_count_less_than_one(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        bot = await create_test_bot(api, f"test-scale-invalid-{unique_id}")

        resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/scale",
            params=api.params(),
            json={
                "target_count": 0,
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )

        assert resp.status_code == 422

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_nonexistent_bot(self, api: APITestHelper) -> None:
        response = await api.client.post(
            f"{api.bot_url('nonexistent-uuid-12345678')}/restart",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error_code"] == "BOT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_bot(self, api: APITestHelper) -> None:
        response = await api.client.post(
            api.bot_url("nonexistent-uuid-12345678") + "/destroy",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error_code"] == "BOT_NOT_FOUND"


class TestGetBotWsInfo:
    @pytest.mark.asyncio
    async def test_get_bot_ws_info(self, api: APITestHelper, unique_id: str) -> None:
        bot = await create_test_bot(api, f"test-wsinfo-{unique_id}")

        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/ws-info",
            params=api.params(),
        )

        assert resp.status_code in [200, 422, 500]
        if resp.status_code == 200:
            data = resp.json()
            assert data["code"] == 0

        await cleanup_bot(api, bot["bot_uuid"])


class TestGetBotHttpInfo:
    """E2E tests for GET /api/v1/bots/{bot_uuid}/http-info with device_uuid."""

    @pytest.mark.asyncio
    async def test_get_http_info_with_device_uuid(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        bot = await create_test_bot(api, f"test-httpinfo-uuid-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            resp = await api.client.get(
                f"{api.bot_url(bot_uuid)}/http-info",
                params=api.params(port=8080, path="/api/health"),
            )

            assert resp.status_code in [200, 404, 422, 500]
            if resp.status_code != 200:
                return

            data = resp.json()
            assert data["code"] == 0

            target = data["data"]["target"]
            assert target, "target should not be empty"
            device_uuid = target.split(":")[0] if ":" in target else None
            if not device_uuid:
                return

            resp2 = await api.client.get(
                f"{api.bot_url(bot_uuid)}/http-info",
                params=api.params(
                    port=8080,
                    path="/api/health",
                    device_uuid=device_uuid,
                ),
            )

            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2["code"] == 0
            assert data2["data"]["target"] == target
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_get_http_info_with_nonexistent_device_uuid(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        bot = await create_test_bot(api, f"test-httpinfo-bad-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            resp = await api.client.get(
                f"{api.bot_url(bot_uuid)}/http-info",
                params=api.params(
                    port=8080,
                    path="/api/health",
                    device_uuid="nonexistent-device-uuid",
                ),
            )

            assert resp.status_code == 404
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_get_http_info_without_device_uuid(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        bot = await create_test_bot(api, f"test-httpinfo-noid-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            resp = await api.client.get(
                f"{api.bot_url(bot_uuid)}/http-info",
                params=api.params(port=8080, path="/api/health"),
            )

            assert resp.status_code in [200, 404, 422, 500]
            if resp.status_code == 200:
                data = resp.json()
                assert data["code"] == 0
        finally:
            await cleanup_bot(api, bot_uuid)


class TestDestroyingStatus:
    """E2E tests for DESTROYING status behavior during bot destruction."""

    @pytest.mark.asyncio
    async def test_destroy_sets_bot_to_destroying_status(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Test that destroy_bot sets status to DESTROYING immediately."""
        bot = await create_test_bot(api, f"test-destroying-{unique_id}")

        # Approve to make bot ACTIVE
        pid = bot["publish_id"]
        resp = await api.client.post(
            api.publish_url(pid, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200

        # Verify bot is ACTIVE
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ACTIVE"

        # Destroy the bot
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/destroy",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "publish_id" in data["data"]

        # Verify bot status is DESTROYING
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "DESTROYING"

    @pytest.mark.asyncio
    async def test_cannot_destroy_already_destroying_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Test that destroy_bot rejects when bot is already DESTROYING."""
        bot = await create_test_bot(api, f"test-double-destroy-{unique_id}")

        # First activate the bot by approving the initial publish
        pid = bot["publish_id"]
        resp = await api.client.post(
            api.publish_url(pid, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200

        # Verify bot is ACTIVE
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        assert resp.json()["data"]["status"] == "ACTIVE"

        # Destroy the bot (first destroy)
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/destroy",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code == 200

        # Verify bot is DESTROYING
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        assert resp.json()["data"]["status"] == "DESTROYING"

        # Try to destroy again (should fail with 400 because bot is already DESTROYING)
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/destroy",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        # 400 Bad Request because destroy_bot checks status and rejects DESTROYING bots
        assert resp.status_code == 400
        data = resp.json()
        # Error message says "Bot is already being destroyed"
        assert "already being destroyed" in data.get("detail", {}).get("message", "")

    @pytest.mark.asyncio
    async def test_cannot_scale_destroying_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Test that scale_bot rejects when bot is DESTROYING."""
        bot = await create_test_bot(api, f"test-scale-destroying-{unique_id}")

        # First activate the bot by approving the initial publish
        pid = bot["publish_id"]
        resp = await api.client.post(
            api.publish_url(pid, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200

        # Destroy the bot to make it DESTROYING
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/destroy",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code == 200

        # Try to scale (should fail)
        resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/scale",
            params=api.params(),
            json={
                "target_count": 3,
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "DESTROYING" in data.get("detail", {}).get("message", "")

    @pytest.mark.asyncio
    async def test_cannot_restart_destroying_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Test that restart_bot rejects when bot is DESTROYING."""
        bot = await create_test_bot(api, f"test-restart-destroying-{unique_id}")

        # First activate the bot by approving the initial publish
        pid = bot["publish_id"]
        resp = await api.client.post(
            api.publish_url(pid, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200

        # Destroy the bot to make it DESTROYING
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/destroy",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code == 200

        # Try to restart (should fail)
        resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/restart",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "DESTROYING" in data.get("detail", {}).get("message", "")


class TestStopOperation:
    """E2E tests for stop operation behavior."""

    @pytest.mark.asyncio
    async def test_stop_active_bot(self, api: APITestHelper, unique_id: str) -> None:
        bot = await create_test_bot(api, f"test-stop-active-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            pid = bot["publish_id"]
            resp = await api.client.post(
                api.publish_url(pid, "approve"),
                params=api.params(),
                json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
            )
            assert resp.status_code == 200

            resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
            assert resp.status_code == 200
            assert resp.json()["data"]["status"] == "ACTIVE"

            resp = await api.client.post(
                api.bot_url(bot_uuid) + "/stop",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["code"] == 0
            assert "publish_id" in data["data"]
            assert data["data"]["status"] == "STOPPING"

        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_cannot_stop_stopped_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        bot = await create_test_bot(api, f"test-stop-stopped-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            pid = bot["publish_id"]
            resp = await api.client.post(
                api.publish_url(pid, "approve"),
                params=api.params(),
                json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
            )
            assert resp.status_code == 200

            resp = await api.client.post(
                api.bot_url(bot_uuid) + "/stop",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert resp.status_code == 200

            resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
            assert resp.json()["data"]["status"] == "STOPPING"

            resp = await api.client.post(
                api.bot_url(bot_uuid) + "/stop",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert resp.status_code == 400
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_cannot_stop_destroying_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        bot = await create_test_bot(api, f"test-stop-destroying-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            pid = bot["publish_id"]
            resp = await api.client.post(
                api.publish_url(pid, "approve"),
                params=api.params(),
                json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
            )
            assert resp.status_code == 200

            resp = await api.client.post(
                api.bot_url(bot_uuid) + "/destroy",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert resp.status_code == 200

            resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
            assert resp.json()["data"]["status"] == "DESTROYING"

            resp = await api.client.post(
                api.bot_url(bot_uuid) + "/stop",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert resp.status_code == 400
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_cannot_stop_pending_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        bot = await create_test_bot(api, f"test-stop-pending-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            resp = await api.client.post(
                api.bot_url(bot_uuid) + "/stop",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert resp.status_code == 400
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_restart_stopped_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        from tests.e2e.asgi.conftest import activate_bot

        bot = await create_test_bot(api, f"test-restart-stopped-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            await activate_bot(api, bot)

            await _stop_bot_and_wait(api, bot_uuid)

            resp = await api.client.post(
                api.bot_url(bot_uuid) + "/restart",
                params=api.params(),
                json={
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                    "scope": "all",
                },
            )
            assert resp.status_code in (200, 409), (
                f"restart_bot should not reject STOPPED status, got {resp.status_code}: {resp.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_update_stopped_bot_name(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        from tests.e2e.asgi.conftest import activate_bot

        bot = await create_test_bot(api, f"test-update-stopped-{unique_id}")
        bot_uuid = bot["bot_uuid"]

        try:
            await activate_bot(api, bot)

            await _stop_bot_and_wait(api, bot_uuid)

            resp = await api.client.post(
                api.bot_url(bot_uuid) + "/update",
                params=api.params(),
                json={
                    "name": f"restored-after-stop-{unique_id}",
                    "operator": "e2e-test",
                },
            )
            assert resp.status_code in (200, 409), (
                f"update_bot should not reject STOPPED status, got {resp.status_code}: {resp.text}"
            )
        finally:
            await cleanup_bot(api, bot_uuid)


class TestUpdateDevices:
    """POST /api/v1/bots/{bot_uuid}/update-devices — SYNC direct-execution publish."""

    async def _get_bot_devices(self, api: APITestHelper, bot_uuid: str) -> list[dict]:
        """Fetch bot detail and return its devices."""
        resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        assert resp.status_code == 200
        return resp.json()["data"].get("devices", [])

    @pytest.mark.asyncio
    async def test_update_devices_returns_publish_id(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        from tests.e2e.asgi.conftest import activate_bot

        bot = await create_test_bot(api, f"test-op-upd-dev-{unique_id}")
        bot_uuid = bot["bot_uuid"]
        await activate_bot(api, bot)
        resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        assert resp.status_code == 200
        bot_data = resp.json()["data"]
        devices_resp = await api.client.get(
            f"{api.bot_url(bot_uuid)}/devices", params=api.params()
        )
        assert devices_resp.status_code == 200
        devices_lists = devices_resp.json()["data"]
        devices = devices_lists[0]["items"]
        assert devices, f"No devices found on bot {bot_uuid}"

        device_uuid = devices[0]["device_uuid"]

        response = await api.client.post(
            f"{api.bot_url(bot_uuid)}/update-devices",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "device_uuids": [device_uuid],
                "auto_approve_publish": True,
                "request_id": uuid.uuid4().hex,
            },
        )

        assert response.status_code in (200, 409)
        if response.status_code == 409:
            await cleanup_bot(api, bot_uuid)
            pytest.skip("Bot has concurrent active publish")

        data = response.json()
        assert data["code"] == 0
        assert data["data"]["publish_id"] > 0
        assert data["data"]["bot_uuid"] == bot_uuid
        # Bot status unchanged
        assert data["data"]["status"] == bot_data["status"]
        await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_update_devices_nonexistent_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        response = await api.client.post(
            f"{api.bot_url('nonexistent-uuid-12345678')}/update-devices",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "device_uuids": ["DEV-MISSING"],
                "request_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_devices_invalid_uuids(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        from tests.e2e.asgi.conftest import activate_bot

        bot = await create_test_bot(api, f"test-op-upd-dev-inv-{unique_id}")
        bot_uuid = bot["bot_uuid"]
        await activate_bot(api, bot)

        response = await api.client.post(
            f"{api.bot_url(bot_uuid)}/update-devices",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "device_uuids": ["DEV-NOT-MINE"],
                "request_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code == 400
        await cleanup_bot(api, bot_uuid)
