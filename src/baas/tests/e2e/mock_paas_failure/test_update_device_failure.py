"""E2E tests for mock PaaS update-device failure.

When PAAS_MOCK_CREATE_FAILURE=true, MockPaasService.create_device()
raises PaasError(DEVICE_CREATION_FAILED).

UPDATE_DEVICE reuses _execute_update_batch() which does a
destroy→create cycle per device. The create step can fail.

Behaviors to verify:
- UPDATE_DEVICE: create_device fails in the update cycle → device FAILED, publish FAILED
- Bot record status remains unchanged (UPDATE_DEVICE doesn't modify bot record)

Requires:
- Service running with PAAS_MOCK_MODE=true + PAAS_MOCK_CREATE_FAILURE=true
  (just restart-mock-failure-create)
"""

import uuid

import pytest

from ..conftest import APITestHelper, cleanup_bot
from ..hook_helpers import (
    create_and_activate_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e, pytest.mark.mock_paas_create_failure]


class TestUpdateDeviceFailure:
    """create_device fails during UPDATE_DEVICE publish execution."""

    @pytest.mark.asyncio
    async def test_update_device_publish_fails_on_device_create(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """UPDATE_DEVICE: device creation fails → publish FAILED, bot status unchanged."""
        bot = await create_and_activate_bot(
            api, f"upd-dev-fail-{unique_id}", device_count=1
        )
        bot_uuid = bot["bot_uuid"]

        resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        assert resp.status_code == 200
        bot_data = resp.json()["data"]
        original_status = bot_data["status"]

        devices = bot_data.get("devices", [])
        if not devices:
            await cleanup_bot(api, bot_uuid)
            pytest.skip(f"No devices found for bot {bot_uuid}")

        device_uuid = devices[0]["device_uuid"]
        resp = await api.client.post(
            f"{api.bot_url(bot_uuid)}/update-devices",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "device_uuids": [device_uuid],
                "auto_approve_publish": True,
                "request_id": uuid.uuid4().hex,
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

        # Bot status unchanged even when publish fails
        assert data["data"]["status"] == original_status

        # Wait for publish to fail (direct execution)
        publish_status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert publish_status == "FAILED", f"Expected FAILED, got {publish_status}"

        # Bot status still unchanged after publish failure
        resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        assert resp.json()["data"]["status"] == original_status

        # Device records reflect the failure
        devices = await get_devices_from_progress(api, publish_id)
        if devices:
            # If records were created before failure, they should be FAILED
            assert all(d["result_status"] == "FAILED" for d in devices), (
                f"Expected all device records FAILED on create failure, got: "
                f"{[d['result_status'] for d in devices]}"
            )

        await cleanup_bot(api, bot_uuid)
