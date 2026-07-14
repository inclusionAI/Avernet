"""E2E tests for auto_approve_publish flag with update-devices operation.

Verifies that when auto_approve_publish=True, the update-devices call creates
a direct-execution UPDATE_DEVICE publish that runs through immediately.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import logging
import time

import pytest

from ..conftest import APITestHelper, cleanup_bot, create_test_bot
from ..hook_helpers import (
    get_devices_from_progress,
    wait_for_publish_status,
)

tlog = logging.getLogger("e2e.auto_approve")

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestAutoApprovePublishUpdateDevice:
    """update_devices with auto_approve_publish=True."""

    async def _fetch_bot(self, api: APITestHelper, bot_uuid: str) -> dict:
        resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        assert resp.status_code == 200
        return resp.json()["data"]

    @pytest.mark.asyncio
    async def test_update_devices_with_auto_approve(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Update devices with auto_approve_publish=True creates direct-execution publish."""
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"aa-upd-dev-{unique_id}",
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

        # Fetch bot to get devices
        bot_data = await self._fetch_bot(api, bot_uuid)
        assert bot_data["status"] == "ACTIVE"

        devices = bot_data.get("devices", [])
        if not devices:
            await cleanup_bot(api, bot_uuid)
            pytest.skip(f"No devices returned for bot {bot_uuid}")

        device_uuid = devices[0]["device_uuid"]
        resp = await api.client.post(
            api.bot_url(bot_uuid) + "/update-devices",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "device_uuids": [device_uuid],
                "auto_approve_publish": True,
                "request_id": __import__("uuid").uuid4().hex,
            },
        )
        assert resp.status_code in (200, 409)
        if resp.status_code == 409:
            await cleanup_bot(api, bot_uuid)
            pytest.skip("Bot has concurrent active publish")

        data = resp.json()
        assert data["code"] == 0
        publish_id = data["data"]["publish_id"]
        assert publish_id > 0

        # Bot status unchanged
        assert data["data"]["status"] == "ACTIVE"

        # Wait for publish to complete (direct execution)
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status in ("SUCCESS", "FAILED"), (
            f"UPDATE_DEVICE publish expected SUCCESS or FAILED, got {status}"
        )

        # Bot status still unchanged after completion
        bot_data = await self._fetch_bot(api, bot_uuid)
        assert bot_data["status"] == "ACTIVE"

        # Device records exist with proper result_status
        progress_devices = await get_devices_from_progress(api, publish_id)
        assert len(progress_devices) == 1, (
            f"Expected 1 device record, got {len(progress_devices)}"
        )
        assert progress_devices[0]["result_status"] in ("SUCCESS", "FAILED"), (
            f"Unexpected result_status: {progress_devices[0]['result_status']}"
        )

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot_uuid)
