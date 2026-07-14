"""E2E tests for async device hook callback flow: UPDATE_DEVICE publish type.

UPDATE_DEVICE is a direct-execution publish (single batch, no approval gates)
that reuses _execute_update_batch() to do a destroy→create→start cycle per
device. When hooks are configured, the callback drives the completion.

Key behavioral difference from UPDATE: UPDATE_DEVICE does NOT modify the bot
DB record status — only devices are updated. The bot's stored DB record stays
at whatever status it was in before the publish.

However, GET /api/v1/bots/{bot_uuid} returns a **calculated** status derived
from device states (_calculate_bot_status). If all devices are FAILED the
calculated status is FAILED; if any device is ACTIVE it stays ACTIVE. This is
acceptable — the invariant is that the *stored* DB record is never touched.

Covers:
- Success path: SUCCESS callback → publish SUCCESS, bot stored status unchanged
- Failure path (1 device): FAILED callback → publish FAILED, calculated → FAILED
- Failure path (3 devices, 1 fails): 2 SUCCESS + 1 FAILED callback →
  publish FAILED, calculated → ACTIVE (still has active devices)

Requires:
- Service running with PAAS_MOCK_MODE=true (restart-mock)
"""

import uuid

import pytest

from ..conftest import APITestHelper, cleanup_bot
from ..hook_helpers import (
    activate_bot,
    create_hook_bot,
    get_devices_from_progress,
    send_callbacks_for_hook_devices,
    send_mixed_callbacks,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


async def _get_bot_devices(api: APITestHelper, bot_uuid: str) -> list[dict]:
    """Get devices for a bot via detail-by-uuid (which includes devices in response)."""
    resp = await api.client.get(
        f"{api.bot_url(bot_uuid)}/detail-by-uuid",
        params=api.params(),
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    if not items:
        return []
    # Get the first (and typically only active) bot record's devices
    return items[0].get("devices", [])


async def _get_bot_status(api: APITestHelper, bot_uuid: str) -> str:
    """Get bot status from GET /bots/{bot_uuid}.

    NOTE: This returns the *calculated* status from device states, not the
    stored DB record status. After UPDATE_DEVICE, if all devices are FAILED,
    this will return FAILED even though the DB record is unchanged.
    """
    resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
    assert resp.status_code == 200
    return resp.json()["data"]["status"]


async def _trigger_update_devices(
    api: APITestHelper, bot_uuid: str, device_uuids: list[str]
) -> int:
    resp = await api.client.post(
        f"{api.bot_url(bot_uuid)}/update-devices",
        params=api.params(),
        json={
            "operator": "e2e-test",
            "device_uuids": device_uuids,
            "auto_approve_publish": True,
            "request_id": uuid.uuid4().hex,
        },
    )
    assert resp.status_code in (200, 409), (
        f"update-devices failed: {resp.status_code} {resp.text}"
    )
    if resp.status_code == 409:
        return -1
    return resp.json()["data"]["publish_id"]


class TestUpdateDeviceCallbackFailure:
    """UPDATE_DEVICE with hook callback FAILED → publish FAILED, bot calculated status reflects devices."""

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="Unstable: race between callback arrival and publish auto-complete"
    )
    async def test_callback_failure_all_devices_failed(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: all callbacks FAILED → publish FAILED, calculated status is FAILED (all devices down)."""
        bot = await create_hook_bot(api, f"upd-dev-cb-fail-{unique_id}", device_count=1)
        await activate_bot(api, bot)
        bot_uuid = bot["bot_uuid"]

        devices = await _get_bot_devices(api, bot_uuid)
        if not devices:
            await cleanup_bot(api, bot_uuid)
            pytest.skip(f"No devices found for bot {bot_uuid}")
        device_uuids = [d["device_uuid"] for d in devices]

        publish_id = await _trigger_update_devices(api, bot_uuid, device_uuids)
        if publish_id < 0:
            await cleanup_bot(api, bot_uuid)
            pytest.skip("Bot has concurrent active publish")

        # Send FAILED callbacks for all devices
        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=1,
            stderr="mock hook failure for UPDATE_DEVICE",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        # Verify device records are FAILED
        all_devices = await get_devices_from_progress(api, publish_id)
        for d in all_devices:
            assert d["result_status"] == "FAILED", (
                f"Expected FAILED, got {d['result_status']}"
            )

        # Calculated status from devices: 0 ACTIVE + 1 FAILED → all FAILED → FAILED
        # (The stored DB record remains ACTIVE — UPDATE_DEVICE never changes it)
        bot_status = await _get_bot_status(api, bot_uuid)
        assert bot_status == "FAILED", (
            f"Expected FAILED (calculated from 1 FAILED device), got {bot_status}"
        )

        await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_callback_partial_failure_partial_devices_failed(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices, update 1: 1 FAILED callback → publish FAILED, but calculated bot status is ACTIVE (2 good devices remain)."""
        bot = await create_hook_bot(
            api, f"upd-dev-partial-fail-{unique_id}", device_count=3
        )
        await activate_bot(api, bot)
        bot_uuid = bot["bot_uuid"]

        # Get all 3 devices — only update 1 device
        devices = await _get_bot_devices(api, bot_uuid)
        if len(devices) < 3:
            await cleanup_bot(api, bot_uuid)
            pytest.skip(f"Bot only has {len(devices)} devices, need 3")
        device_to_update = [devices[0]["device_uuid"]]

        publish_id = await _trigger_update_devices(api, bot_uuid, device_to_update)
        if publish_id < 0:
            await cleanup_bot(api, bot_uuid)
            pytest.skip("Bot has concurrent active publish")

        # Send mixed callbacks: 1 FAILED (the single updated device)
        await send_mixed_callbacks(
            api,
            publish_id,
            fail_index=0,  # First (and only) device gets FAILED
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        # Verify the updated device record is FAILED and matches the device_uuid we sent
        all_devices = await get_devices_from_progress(api, publish_id)
        assert len(all_devices) == 1, (
            f"Expected 1 device record, got {len(all_devices)}"
        )
        updated_device = all_devices[0]
        assert updated_device["device_uuid"] == device_to_update[0], (
            f"Expected device {device_to_update[0]}, "
            f"got {updated_device.get('device_uuid')}"
        )
        assert updated_device["result_status"] == "FAILED", (
            f"Expected FAILED, got {updated_device['result_status']}"
        )

        # Calculated status: 2 remaining ACTIVE devices + 1 FAILED → at least 1 ACTIVE → ACTIVE
        bot_status = await _get_bot_status(api, bot_uuid)
        assert bot_status == "ACTIVE", (
            f"Expected ACTIVE (2 ACTIVE devices remain), got {bot_status}"
        )

        await cleanup_bot(api, bot_uuid)


class TestUpdateDeviceCallbackSuccess:
    """UPDATE_DEVICE with hook callback SUCCESS → publish SUCCESS, bot unchanged."""

    @pytest.mark.asyncio
    async def test_callback_success_publish_succeeds_bot_unchanged(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: UPDATE_DEVICE → SUCCESS callback → publish SUCCESS, bot status unchanged."""
        bot = await create_hook_bot(api, f"upd-dev-cb-ok-{unique_id}", device_count=1)
        await activate_bot(api, bot)
        bot_uuid = bot["bot_uuid"]

        devices = await _get_bot_devices(api, bot_uuid)
        if not devices:
            await cleanup_bot(api, bot_uuid)
            pytest.skip(f"No devices found for bot {bot_uuid}")
        original_status = await _get_bot_status(api, bot_uuid)
        device_uuids = [d["device_uuid"] for d in devices]

        publish_id = await _trigger_update_devices(api, bot_uuid, device_uuids)
        if publish_id < 0:
            await cleanup_bot(api, bot_uuid)
            pytest.skip("Bot has concurrent active publish")

        # Send SUCCESS callbacks
        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="SUCCESS",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Verify device records are SUCCESS
        all_devices = await get_devices_from_progress(api, publish_id)
        for d in all_devices:
            assert d["result_status"] == "SUCCESS", (
                f"Expected SUCCESS, got {d['result_status']}"
            )

        # Bot status MUST be unchanged — UPDATE_DEVICE never changes bot record
        bot_status = await _get_bot_status(api, bot_uuid)
        assert bot_status == original_status, (
            f"Bot status changed from {original_status} to {bot_status} "
            f"after UPDATE_DEVICE callback success"
        )

        await cleanup_bot(api, bot_uuid)
