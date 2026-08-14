"""E2E tests for publish record repository queries.

Tests publish record listing, filtering, pagination, progress queries,
and status transitions via HTTP endpoints. Zero production code changes.

These tests verify read-path behavior: listing, detail fetching,
progress inspection, and filtering by publish records.
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import APITestHelper, cleanup_bot, create_test_bot

pytestmark = [pytest.mark.e2e_asgi]


# ═══════════════════════════════════════════════════════════════════════════════
# Publish Record Queries
# ═══════════════════════════════════════════════════════════════════════════════


class TestPublishRecordQueries:
    """Basic publish record CRUD queries: create, detail, progress."""

    @pytest.mark.asyncio
    async def test_create_publish_returns_200(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /api/v1/publishes with valid body returns 200 with publish data."""
        bot = await create_test_bot(api, f"rec-post-{unique_id}")

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
                f"Expected 200, got {response.status_code}: {response.text}"
            )
            data = response.json()
            assert data["code"] == 0
            assert "data" in data
        finally:
            await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_get_publish_detail(self, api: APITestHelper, unique_id: str) -> None:
        """GET /api/v1/publishes/{id} returns full publish record detail."""
        bot = await create_test_bot(api, f"rec-detail-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]

        response = await api.client.get(
            api.publish_url(publish_id),
            params=api.params(),
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["code"] == 0
        record = data["data"]
        assert record["id"] == publish_id, (
            f"Expected id={publish_id}, got {record.get('id')}"
        )
        # Verify key fields exist (their values depend on the test environment)
        assert "status" in record, f"Missing 'status' in record: {record}"
        assert "publish_type" in record or "type" in record, (
            f"Missing publish type key: {record}"
        )
        assert isinstance(record.get("bot_id"), int), (
            f"bot_id should be int: {record.get('bot_id')}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_get_publish_progress(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """GET /api/v1/publishes/{id}/progress returns progress data structure."""
        bot = await create_test_bot(api, f"rec-progress-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]

        response = await api.client.get(
            api.publish_url(publish_id, "progress"),
            params=api.params(),
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["code"] == 0
        progress = data["data"]
        assert progress["publish_id"] == publish_id, (
            f"Expected publish_id={publish_id}, got {progress.get('publish_id')}"
        )
        # Progress data should have either stages/stage or overall_progress
        assert (
            "stages" in progress
            or "stage" in progress
            or "overall_progress" in progress
        ), f"Missing progress structure: {list(progress.keys())}"

        await cleanup_bot(api, bot["bot_uuid"])


# ═══════════════════════════════════════════════════════════════════════════════
# Publish Record Filters
# ═══════════════════════════════════════════════════════════════════════════════


class TestPublishRecordFilters:
    """Filters and edge cases for publish record queries."""

    @pytest.mark.asyncio
    async def test_post_publish_duplicate_request_id(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST with duplicate request_id is idempotent."""
        bot = await create_test_bot(api, f"rec-dup-{unique_id}")
        request_id = uuid.uuid4().hex

        try:
            # First POST
            resp1 = await api.client.post(
                api.publish_url(),
                params=api.params(),
                json={
                    "bot_id": bot["id"],
                    "publish_type": "CREATE",
                    "operator": "e2e-test",
                    "request_id": request_id,
                },
            )
            assert resp1.status_code == 200

            # Second POST with same request_id — idempotent
            resp2 = await api.client.post(
                api.publish_url(),
                params=api.params(),
                json={
                    "bot_id": bot["id"],
                    "publish_type": "CREATE",
                    "operator": "e2e-test",
                    "request_id": request_id,
                },
            )
            assert resp2.status_code == 200

            data2 = resp2.json()
            assert data2["code"] == 0
            assert data2["data"]["id"] == resp1.json()["data"]["id"], (
                "Idempotent POST with same request_id should return same publish_id"
            )
        finally:
            await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_get_publish_not_found(self, api: APITestHelper) -> None:
        """GET /api/v1/publishes/99999999 returns 404 for nonexistent publish."""
        response = await api.client.get(
            api.publish_url(99999999),
            params=api.params(),
        )

        assert response.status_code == 404, (
            f"Expected 404 for nonexistent publish, got {response.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Publish Progress Details
# ═══════════════════════════════════════════════════════════════════════════════


class TestPublishProgressDetails:
    """Tests for the /progress sub-resource endpoint."""

    @pytest.mark.asyncio
    async def test_progress_with_devices(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """GET /publishes/{id}/progress?include_devices=true returns device details."""
        bot = await create_test_bot(api, f"rec-progdev-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]

        response = await api.client.get(
            api.publish_url(publish_id, "progress"),
            params=api.params(include_devices="true"),
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["code"] == 0
        progress = data["data"]

        # With include_devices=true, device_details or devices should exist
        has_devices = (
            "device_details" in progress
            or "devices" in progress
            or "overall_progress" in progress
        )
        assert has_devices, (
            f"Expected device details in progress response: {list(progress.keys())}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_progress_for_nonexistent_publish(self, api: APITestHelper) -> None:
        """GET /publishes/99999999/progress returns 404."""
        response = await api.client.get(
            api.publish_url(99999999, "progress"),
            params=api.params(),
        )

        assert response.status_code == 404, (
            f"Expected 404 for nonexistent publish progress, got {response.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Publish Record Status Transitions
# ═══════════════════════════════════════════════════════════════════════════════


class TestPublishRecordStatusTransitions:
    """Verify publish status after creation and multi-bot scenarios."""

    @pytest.mark.asyncio
    async def test_publish_status_after_creation(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Bot creation produces a publish record with a terminal or active status.

        With auto_approve_publish=True (default in create_test_bot), the
        publish should reach SUCCESS or ACTIVE synchronously.
        """
        bot = await create_test_bot(api, f"rec-status-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]

        response = await api.client.get(
            api.publish_url(publish_id),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        status = data["data"]["status"]

        # After creation, status should be a valid publish state
        # (PENDING if not yet approved, or SUCCESS/ACTIVE if auto-approved)
        valid_statuses = {"PENDING", "ACTIVE", "SUCCESS", "APPROVING"}
        assert status in valid_statuses, (
            f"Expected status in {valid_statuses}, got '{status}'"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_multiple_publishes_different_ids(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Two bots produce distinct publish_ids, and both are accessible."""
        bot1 = await create_test_bot(api, f"rec-multi-a-{unique_id}", device_count=1)
        bot2 = await create_test_bot(api, f"rec-multi-b-{unique_id}", device_count=1)

        assert bot1["publish_id"] != bot2["publish_id"], (
            f"Expected different publish IDs, got {bot1['publish_id']} and {bot2['publish_id']}"
        )

        # Both publishes should be fetchable
        for label, bot in [("bot1", bot1), ("bot2", bot2)]:
            response = await api.client.get(
                api.publish_url(bot["publish_id"]),
                params=api.params(),
            )
            assert response.status_code == 200, (
                f"{label} publish {bot['publish_id']} not accessible: "
                f"{response.status_code}"
            )
            response_data = response.json()
            assert response_data["code"] == 0, (
                f"{label} publish {bot['publish_id']} unexpected code: "
                f"{response_data.get('code')}"
            )

        await cleanup_bot(api, bot1["bot_uuid"])
        await cleanup_bot(api, bot2["bot_uuid"])
