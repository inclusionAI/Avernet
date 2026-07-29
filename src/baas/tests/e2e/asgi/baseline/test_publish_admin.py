"""E2E tests for admin service + publish repository coverage.

Targets:
- /api/v1/admin/force-success — force-success on real/fake publishes
- /api/v1/publishes/{id}/{action} — admin-level publish operations (retry, revoke, reject)
- /api/v1/publishes — publish list queries (all, paginated, single)

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import time
import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    approve_publish,
    cleanup_bot,
    create_hook_bot,
    create_test_bot,
    send_callbacks_for_hook_devices,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e_asgi]


# ═══════════════════════════════════════════════════════════════════════════════
# Admin Force-Success
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdminForceSuccess:
    """Coverage for /api/v1/admin/force-success — validation and error paths."""

    @pytest.mark.asyncio
    async def test_force_success_not_found(self, api: APITestHelper) -> None:
        """POST with nonexistent publish_id returns 404."""
        resp = await api.client.post(
            api.admin_force_success_url(),
            params=api.params(),
            json={"publish_id": 99999999, "modifier": "e2e-test"},
        )
        assert resp.status_code == 404, (
            f"Expected 404 for nonexistent publish_id, "
            f"got {resp.status_code}: {resp.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_force_success_missing_fields(self, api: APITestHelper) -> None:
        """POST with empty body returns 422."""
        resp = await api.client.post(
            api.admin_force_success_url(),
            params=api.params(),
            json={},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for empty body, got {resp.status_code}: {resp.text[:200]}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Admin Publish Operations (retry, revoke, reject)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdminPublishOperations:
    """Coverage for publish action endpoints with nonexistent IDs and validation."""

    @pytest.mark.asyncio
    async def test_retry_publish_not_found(self, api: APITestHelper) -> None:
        """POST /api/v1/publishes/{id}/retry with nonexistent publish_id returns 404/422."""
        resp = await api.client.post(
            api.publish_url(99999999, "retry"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code in (404, 422), (
            f"Expected 404 or 422 for retry nonexistent, "
            f"got {resp.status_code}: {resp.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_revoke_publish_not_found(self, api: APITestHelper) -> None:
        """POST /api/v1/publishes/{id}/revoke with nonexistent publish_id returns 404/422."""
        resp = await api.client.post(
            api.publish_url(99999999, "revoke"),
            params=api.params(),
            json={"operator": "e2e-test"},
        )
        assert resp.status_code in (404, 422), (
            f"Expected 404 or 422 for revoke nonexistent, "
            f"got {resp.status_code}: {resp.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_reject_publish_not_found(self, api: APITestHelper) -> None:
        """POST /api/v1/publishes/{id}/reject with nonexistent publish_id returns 404/422."""
        resp = await api.client.post(
            api.publish_url(99999999, "reject"),
            params=api.params(),
            json={"operator": "e2e-test", "reason": "test reject"},
        )
        assert resp.status_code in (404, 422), (
            f"Expected 404 or 422 for reject nonexistent, "
            f"got {resp.status_code}: {resp.text[:200]}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Admin Publish List / Query
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdminPublishListQueries:
    """Coverage for publish repository query endpoints (single lookup + admin operations)."""

    @pytest.mark.asyncio
    async def test_create_publish_with_valid_body(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /api/v1/publishes with valid body returns 200."""
        bot = await create_test_bot(api, f"admin-post-{unique_id}")

        try:
            resp = await api.client.post(
                api.publish_url(),
                params=api.params(),
                json={
                    "bot_id": bot["id"],
                    "publish_type": "CREATE",
                    "operator": "e2e-test",
                    "request_id": uuid.uuid4().hex,
                },
            )
            assert resp.status_code == 200, (
                f"Expected 200 for publish creation, "
                f"got {resp.status_code}: {resp.text[:200]}"
            )
            data = resp.json()
            assert data["code"] == 0
            assert "data" in data
        finally:
            await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_post_publish_missing_body(self, api: APITestHelper) -> None:
        """POST /api/v1/publishes with empty body returns 422."""
        resp = await api.client.post(
            api.publish_url(),
            params=api.params(),
            json={},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for empty body, got {resp.status_code}: {resp.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_get_single_publish(self, api: APITestHelper, unique_id: str) -> None:
        """Create a bot, then GET its publish by ID."""
        t0 = time.monotonic()

        bot = await create_test_bot(api, f"admin-pub-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]

        resp = await api.client.get(
            api.publish_url(publish_id),
            params=api.params(),
        )
        assert resp.status_code == 200, (
            f"Expected 200 for single publish, "
            f"got {resp.status_code}: {resp.text[:200]}"
        )
        data = resp.json()
        assert data["code"] == 0
        pub = data["data"]
        assert pub["id"] == publish_id
        assert "publish_type" in pub, f"Missing publish_type in {list(pub.keys())}"
        assert "status" in pub, f"Missing status in {list(pub.keys())}"

        print(f"[TIMING] test_get_single_publish: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])


# ═══════════════════════════════════════════════════════════════════════════════
# Force-Success on a Real Failed Publish
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdminForceSuccessWithRealPublish:
    """End-to-end force-success flow: create hook bot → fail → force-success."""

    @pytest.mark.asyncio
    async def test_force_success_on_real_publish(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create hook bot, approve, send FAILED callbacks, then force-success.

        Verifies that admin force-success transitions a FAILED publish to
        SUCCESS, covering the full integration path from publish lifecycle
        through admin intervention.
        """
        t0 = time.monotonic()

        # 1. Create hook bot (hook deploy config ensures callbacks are needed)
        bot = await create_hook_bot(api, f"fs-real-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]

        # 2. Approve the publish
        code = await approve_publish(api, publish_id)
        assert code == 200, f"Approve failed: status={code}"

        # 3. Send FAILED callbacks to trigger publish failure
        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=1,
            stderr="simulated failure for force-success test",
        )

        # 4. Wait for publish to reach FAILED
        status = await wait_for_publish_status(
            api, publish_id, {"FAILED", "APPROVING"}, timeout_seconds=0.5
        )
        assert status in ("FAILED", "APPROVING"), (
            f"Expected FAILED or APPROVING after failed callbacks, got {status}"
        )

        # 5. Call force-success
        resp = await api.client.post(
            api.admin_force_success_url(),
            params=api.params(),
            json={"publish_id": publish_id, "modifier": "e2e-test"},
        )
        assert resp.status_code == 200, (
            f"Expected 200 for force-success, got {resp.status_code}: {resp.text[:200]}"
        )
        result = resp.json()["data"]
        assert result["publish_id"] == publish_id

        # 6. Verify publish becomes SUCCESS (or force-success already returned 200)
        status_after = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status_after == "SUCCESS", (
            f"Expected SUCCESS after force, got {status_after}"
        )

        # 7. Double-check via GET publish
        resp = await api.client.get(api.publish_url(publish_id), params=api.params())
        assert resp.status_code == 200
        pub_data = resp.json()["data"]
        assert pub_data["status"] == "SUCCESS", (
            f"Expected publish status SUCCESS, got {pub_data['status']}"
        )

        print(
            f"[TIMING] test_force_success_on_real_publish: {time.monotonic() - t0:.2f}s"
        )
        await cleanup_bot(api, bot["bot_uuid"])
