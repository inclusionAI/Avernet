"""E2E tests for auto_approve_publish flag with stop operation.

Verifies that when auto_approve_publish=True, the stop_bot call still works
(STOP has 0 gates so auto_approve is effectively a no-op for gate-advancement).

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import logging
import time

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    cleanup_bot,
    create_test_bot,
    wait_for_publish_status,
)

tlog = logging.getLogger("e2e.auto_approve")

pytestmark = [pytest.mark.e2e_asgi]


class TestAutoApprovePublishStop:
    """stop_bot with auto_approve_publish=True."""

    @pytest.mark.asyncio
    async def test_stop_with_auto_approve(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Stop bot with auto_approve_publish=True."""
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-stop-{unique_id}",
            device_count=1,
            auto_approve_publish=True,
        )
        bot_uuid = bot["bot_uuid"]
        tlog.info(f"[TIMING] create_bot+auto_approve: {time.monotonic() - t0:.2f}s")

        # Approve to activate the bot
        pid = bot["publish_id"]
        resp = await api.client.post(
            api.publish_url(pid, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": __import__("uuid").uuid4().hex},
        )
        assert resp.status_code == 200

        # Verify bot is ACTIVE
        resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        assert resp.json()["data"]["status"] == "ACTIVE"

        # Stop the bot with auto_approve_publish
        resp = await api.client.post(
            api.bot_url(bot_uuid) + "/stop",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": __import__("uuid").uuid4().hex,
                "auto_approve_publish": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        tlog.info(f"[TIMING] stop_bot response: {time.monotonic() - t0:.2f}s")

        # Verify bot is STOPPING
        assert data["data"]["status"] == "STOPPING"

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot_uuid)
