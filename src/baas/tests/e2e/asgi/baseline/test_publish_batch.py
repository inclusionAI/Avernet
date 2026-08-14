"""E2E tests for batch publish edge cases.

Covers batch publish creation, record queries, validation, and edge cases.
Tests POST /api/v1/publishes for creating publishes and GET for listing/querying.
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    cleanup_bot,
    create_test_bot,
)

pytestmark = [pytest.mark.e2e_asgi]


class TestBatchPublishCreation:
    """Batch publish creation and basic validation."""

    @pytest.mark.asyncio
    async def test_create_publish_batch_with_one_bot(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create a bot, then POST a CREATE publish batch for it.

        Verifies the publish batch is created with a valid publish_id.
        """
        bot = await create_test_bot(api, f"batch-one-{unique_id}")

        response = await api.client.post(
            api.publish_url(),
            params=api.params(),
            json={
                "bot_id": bot["id"],
                "publish_type": "CREATE",
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )

        assert response.status_code == 200, (
            f"Expected 200 for publish batch creation, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        assert data["code"] == 0
        assert "id" in data["data"], (
            f"Expected publish_id in response data, got keys: {data['data'].keys()}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_publish_batch_with_multiple_bots(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create 2 bots and verify each gets a CREATE publish batch.

        Each bot creation generates its own publish, verify both exist.
        """
        bot1 = await create_test_bot(api, f"batch-multi-1-{unique_id}")
        bot2 = await create_test_bot(api, f"batch-multi-2-{unique_id}")

        publish_id1 = bot1.get("publish_id")
        publish_id2 = bot2.get("publish_id")
        assert publish_id1, "Bot 1 should have a publish_id"
        assert publish_id2, "Bot 2 should have a publish_id"
        assert publish_id1 != publish_id2, (
            f"Expected unique publish_ids, got {publish_id1} == {publish_id2}"
        )

        # Verify each publish exists via GET
        for bot, publish_id in [(bot1, publish_id1), (bot2, publish_id2)]:
            response = await api.client.get(
                api.publish_url(publish_id),
                params=api.params(),
            )
            assert response.status_code == 200, (
                f"Expected 200 for publish {publish_id}, "
                f"got {response.status_code}: {response.text[:200]}"
            )
            data = response.json()
            assert data["code"] == 0
            assert data["data"]["id"] == publish_id

        await cleanup_bot(api, bot1["bot_uuid"])
        await cleanup_bot(api, bot2["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_batch_publish_with_invalid_type(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST with an invalid publish_type should return 400 or 422."""
        bot = await create_test_bot(api, f"batch-badtype-{unique_id}")

        try:
            response = await api.client.post(
                api.publish_url(),
                params=api.params(),
                json={
                    "bot_id": bot["id"],
                    "publish_type": "INVALID_TYPE",
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )

            assert response.status_code in (400, 422), (
                f"Expected 400 or 422 for invalid publish_type, "
                f"got {response.status_code}: {response.text[:200]}"
            )
        finally:
            await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_batch_publish_with_invalid_bot_id(
        self, api: APITestHelper
    ) -> None:
        """POST with a non-existent bot_id should return 400 or 404."""
        response = await api.client.post(
            api.publish_url(),
            params=api.params(),
            json={
                "bot_id": 9999999,
                "publish_type": "CREATE",
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )

        assert response.status_code in (400, 404), (
            f"Expected 400 or 404 for invalid bot_id, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_create_batch_publish_missing_required_fields(
        self, api: APITestHelper
    ) -> None:
        """POST with missing required fields should return 400 or 422."""
        # Missing bot_id
        response = await api.client.post(
            api.publish_url(),
            params=api.params(),
            json={
                "publish_type": "CREATE",
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code in (400, 422), (
            f"Expected 400/422 for missing bot_id, "
            f"got {response.status_code}: {response.text[:200]}"
        )

        # Missing publish_type
        response2 = await api.client.post(
            api.publish_url(),
            params=api.params(),
            json={
                "bot_id": 9999999,
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert response2.status_code in (400, 422), (
            f"Expected 400/422 for missing publish_type, "
            f"got {response2.status_code}: {response2.text[:200]}"
        )


class TestBatchPublishQueries:
    """Batch publish record queries — single lookup and validation."""

    @pytest.mark.asyncio
    async def test_create_publish_returns_200(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /api/v1/publishes with valid body returns 200 and publish_id."""
        bot = await create_test_bot(api, f"batch-post-{unique_id}")

        try:
            response = await api.client.post(
                api.publish_url(),
                params=api.params(),
                json={
                    "bot_id": bot["id"],
                    "publish_type": "CREATE",
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )

            assert response.status_code == 200, (
                f"Expected 200 for publish creation, "
                f"got {response.status_code}: {response.text[:200]}"
            )
            data = response.json()
            assert data["code"] == 0
            assert "data" in data
        finally:
            await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_publish_missing_operator(self, api: APITestHelper) -> None:
        """POST /api/v1/publishes with missing operator returns 422."""
        response = await api.client.post(
            api.publish_url(),
            params=api.params(),
            json={
                "bot_id": 99999,
                "publish_type": "CREATE",
                "request_id": uuid.uuid4().hex,
            },
        )

        assert response.status_code == 422, (
            f"Expected 422 for missing operator, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_get_publish_by_id(self, api: APITestHelper, unique_id: str) -> None:
        """GET /api/v1/publishes/{id} returns the matching publish record."""
        bot = await create_test_bot(
            api,
            f"batch-getbyid-{unique_id}",
            device_count=1,
        )
        publish_id = bot.get("publish_id")
        assert publish_id, "Bot should have a publish_id"

        response = await api.client.get(
            api.publish_url(publish_id),
            params=api.params(),
        )

        assert response.status_code == 200, (
            f"Expected 200 for single publish lookup, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["id"] == publish_id, (
            f"Expected publish_id={publish_id}, got id={data['data']['id']}"
        )

        await cleanup_bot(api, bot["bot_uuid"])
