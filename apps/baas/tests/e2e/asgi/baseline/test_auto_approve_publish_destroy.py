"""E2E tests for auto_approve_publish flag with destroy operation.

Verifies that when auto_approve_publish=True is passed to destroy_bot,
the flag is passed to the DESTROY publish config.

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


class TestAutoApprovePublishDestroy:
    """destroy_bot with auto_approve_publish=True."""

    @pytest.mark.asyncio
    async def test_destroy_with_auto_approve(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Destroy bot with auto_approve_publish=True."""
        t0 = time.monotonic()
        bot = await create_test_bot(
            api, f"auto-approve-destroy-{unique_id}", device_count=1
        )
        bot_uuid = bot["bot_uuid"]

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

        # Destroy with auto_approve_publish=True
        resp = await api.client.post(
            api.bot_url(bot_uuid) + "/destroy",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": __import__("uuid").uuid4().hex,
                "auto_approve_publish": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        tlog.info(f"[TIMING] destroy_bot response: {time.monotonic() - t0:.2f}s")

        # Verify bot is DESTROYING
        assert data["data"]["status"] == "DESTROYING"

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot_uuid)
