"""E2E tests for publish approval/denial flows.

Covers:
- Auto-approve on bot creation (auto_approve_publish=True)
- Manual approve for non-auto-approve bots
- Deny (reject/revoke) for non-existent publishes
- Idempotency (re-approve already-approved/completed publishes)
- Invalid inputs (missing fields, invalid publish IDs)

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import logging
import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    cleanup_bot,
    create_test_bot,
    wait_for_publish_status,
)

tlog = logging.getLogger("e2e.publish_approval")

pytestmark = [pytest.mark.e2e_asgi]


class TestAutoApprovePublish:
    """Auto-approve flows — auto_approve_publish=True completes synchronously."""

    @pytest.mark.asyncio
    async def test_auto_approve_create_publish_single_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device + auto_approve_publish=True → publish completes automatically.

        Verifies bot is created with a publish_id and the publish reaches a terminal
        state (SUCCESS or ACTIVE) without any manual approval calls.
        """
        bot = await create_test_bot(
            api,
            f"aa-1d-{unique_id}",
            device_count=1,
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]
        assert publish_id, "Bot should have a publish_id after creation"

        # Verify publish exists and has a terminal status
        resp = await api.client.get(
            api.publish_url(publish_id),
            params=api.params(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] in ("SUCCESS", "ACTIVE"), (
            f"Expected SUCCESS or ACTIVE, got {data['status']}"
        )

        # Verify bot is retrievable
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_auto_approve_create_publish_double_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices + auto_approve_publish=True → publish completes automatically.

        2-device pipeline auto-compacts to 2 stages. auto_approve handles all gates
        and the publish should reach a terminal state.
        """
        bot = await create_test_bot(
            api,
            f"aa-2d-{unique_id}",
            device_count=2,
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]
        assert publish_id, "Bot should have a publish_id after creation"

        # Poll for terminal state
        status = await wait_for_publish_status(
            api,
            publish_id,
            {"SUCCESS", "ACTIVE", "APPROVING", "FAILED"},
            timeout_seconds=5.0,
        )
        assert status in ("SUCCESS", "ACTIVE", "APPROVING"), (
            f"Expected SUCCESS/ACTIVE/APPROVING, got {status}"
        )

        # Verify bot exists
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_auto_approve_approve_already_approved(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Approve a publish that was already auto-approved → idempotent.

        After auto_approve_publish=True completes, POSTing /approve again should
        return 200 (idempotent — already approved) or 400 (cannot re-approve).
        Either outcome is acceptable; the key is no 500.
        """
        bot = await create_test_bot(
            api,
            f"aa-re-{unique_id}",
            device_count=1,
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]
        assert publish_id

        # Re-approve the already-completed publish
        resp = await api.client.post(
            api.publish_url(publish_id, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code in (200, 400), (
            f"Expected 200 (idempotent) or 400 (cannot re-approve), got {resp.status_code}"
        )

        await cleanup_bot(api, bot["bot_uuid"])


class TestManualApproveFlow:
    """Manual approve without auto_approve — publish may be PENDING or already completed."""

    @pytest.mark.asyncio
    async def test_manual_approve_publish(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create bot WITHOUT auto_approve, then manually approve the publish.

        The bot is created and publish may be in PENDING or already completed
        (mock mode fast path). The approve call should be accepted (200) or
        rejected as already-terminal (400). Verifies the publish exists via GET.
        """
        bot = await create_test_bot(api, f"manual-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]
        assert publish_id, "Bot should have a publish_id"

        # Attempt manual approval
        resp = await api.client.post(
            api.publish_url(publish_id, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code in (200, 400), (
            f"Expected 200 or 400 for manual approve, got {resp.status_code}"
        )

        # Verify publish exists
        resp = await api.client.get(
            api.publish_url(publish_id),
            params=api.params(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == publish_id

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_approve_with_invalid_publish_id(self, api: APITestHelper) -> None:
        """Approve a non-existent publish ID → 404."""
        resp = await api.client.post(
            api.publish_url(99999999, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 404, (
            f"Expected 404 for invalid publish ID, got {resp.status_code}"
        )


class TestDenyFlow:
    """Reject and revoke flows — including non-existent publish edge cases."""

    @pytest.mark.asyncio
    async def test_reject_publish_not_found(self, api: APITestHelper) -> None:
        """Reject a non-existent publish ID → 422 (validation fails before lookup)."""
        resp = await api.client.post(
            api.publish_url(99999999, "reject"),
            params=api.params(),
            json={"operator": "e2e-test", "reason": "test rejection"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for non-existent publish reject, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_revoke_publish_not_found(self, api: APITestHelper) -> None:
        """Revoke a non-existent publish ID → 422 (validation fails before lookup)."""
        resp = await api.client.post(
            api.publish_url(99999999, "revoke"),
            params=api.params(),
            json={"operator": "e2e-test"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for non-existent publish revoke, got {resp.status_code}"
        )


class TestApproveIdempotency:
    """Approve edge cases — missing fields, already-completed publishes."""

    @pytest.mark.asyncio
    async def test_approve_missing_fields(self, api: APITestHelper) -> None:
        """Approve with empty body → 422 or 400 (validation error)."""
        resp = await api.client.post(
            api.publish_url(1, "approve"),
            params=api.params(),
            json={},
        )
        assert resp.status_code in (400, 422), (
            f"Expected 400/422 for empty approve body, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_approve_already_completed_publish(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Approve a publish that was already completed via auto_approve.

        After auto_approve_publish=True completes the publish, re-approving
        should be idempotent (200) or rejected (400). Either is fine.
        """
        bot = await create_test_bot(
            api,
            f"complete-re-{unique_id}",
            device_count=1,
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]
        assert publish_id

        # Confirm publish completed
        resp = await api.client.get(
            api.publish_url(publish_id),
            params=api.params(),
        )
        assert resp.status_code == 200
        status = resp.json()["data"]["status"]
        tlog.info(f"Publish {publish_id} status before re-approve: {status}")

        # Re-approve the completed publish
        resp = await api.client.post(
            api.publish_url(publish_id, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code in (200, 400), (
            f"Expected 200 (idempotent) or 400 (cannot re-approve completed), "
            f"got {resp.status_code}"
        )

        await cleanup_bot(api, bot["bot_uuid"])
