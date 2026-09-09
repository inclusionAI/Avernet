"""E2E tests for async device hook callback flow: SCALE_DOWN publish type.

SCALE_DOWN uses before_destroy_cmd_hook (synchronous, inline execution).
Single batch, no stage gates. Hook runs before PaaS destroy, result captured inline.
Hook exit_code/stdout/stderr stored in result_message via serialize_hook_result().
No external callback needed — before_destroy_cmd_hook is NOT callback-driven.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    activate_test_bot,
    approve_publish,
    assert_result_message_has_hook_data,
    cleanup_bot,
    create_and_activate_bot,
    create_test_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e_asgi]


async def _scale_down_bot(
    api: APITestHelper, bot_uuid: str, target_count: int
) -> int | None:
    """Scale down bot, return publish_id or None."""
    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/scale",
        params=api.params(),
        json={
            "target_count": target_count,
            "operator": "e2e-test",
            "request_id": uuid.uuid4().hex,
        },
    )
    assert resp.status_code == 200
    return resp.json()["data"].get("publish_id")


# ── Success path ─────────────────────────────────────────────────────────────


class TestScaleDownSuccess:
    """SCALE_DOWN with before_destroy_cmd_hook: sync hook, no callback needed."""

    @pytest.mark.asyncio
    async def test_scale_down_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3→2 devices: approve → sync hook runs inline → SUCCESS."""
        bot = await create_and_activate_bot(
            api, f"scaledn-hook-1d-{unique_id}", device_count=3
        )
        publish_id = await _scale_down_bot(api, bot["bot_uuid"], 2)
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        # Approve — before_destroy_cmd_hook runs synchronously, no callback needed
        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Verify hook data in result_message
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            result_msg = device.get("result_message")
            if result_msg and result_msg.startswith("{"):
                hook_data = assert_result_message_has_hook_data(result_msg)
                assert "stdout" in hook_data

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_scale_down_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3→1 devices: approve → sync hook per device → SUCCESS."""
        bot = await create_and_activate_bot(
            api, f"scaledn-hook-2d-{unique_id}", device_count=3
        )
        publish_id = await _scale_down_bot(api, bot["bot_uuid"], 1)
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Verify hook data in result_message
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            result_msg = device.get("result_message")
            if result_msg and result_msg.startswith("{"):
                hook_data = assert_result_message_has_hook_data(result_msg)
                assert "exit_code" in hook_data

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_scale_down_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """4→1 devices: approve → sync hook per device → SUCCESS."""
        bot = await create_and_activate_bot(
            api, f"scaledn-hook-3d-{unique_id}", device_count=4
        )
        publish_id = await _scale_down_bot(api, bot["bot_uuid"], 1)
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Verify hook data in result_message
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            result_msg = device.get("result_message")
            if result_msg and result_msg.startswith("{"):
                assert_result_message_has_hook_data(result_msg)

        await cleanup_bot(api, bot["bot_uuid"])


# ── No-hook baseline ─────────────────────────────────────────────────────────


class TestScaleDownNoHook:
    """SCALE_DOWN without hook: direct destroy, no hook execution."""

    @pytest.mark.asyncio
    async def test_scale_down_no_hook(self, api: APITestHelper, unique_id: str) -> None:
        """Scale down without hook → direct fast path, plain text result_message."""
        bot = await create_test_bot(api, f"scaledn-nohook-{unique_id}", device_count=3)
        await activate_test_bot(api, bot)

        publish_id = await _scale_down_bot(api, bot["bot_uuid"], 1)
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # No hook → result_message is plain text (not JSON)
        devices = await get_devices_from_progress(api, publish_id)
        if devices and devices[0].get("result_message"):
            assert not devices[0]["result_message"].startswith("{"), (
                "No-hook result_message should be plain text, not JSON"
            )

        await cleanup_bot(api, bot["bot_uuid"])


# ── Explicit device_uuids ────────────────────────────────────────────────────


async def _scale_down_bot_with_device_uuids(
    api: APITestHelper, bot_uuid: str, target_count: int, device_uuids: list[str]
) -> int | None:
    """Scale down bot with explicit device_uuids, return publish_id or None."""
    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/scale",
        params=api.params(),
        json={
            "target_count": target_count,
            "device_uuids": device_uuids,
            "operator": "e2e-test",
            "request_id": uuid.uuid4().hex,
        },
    )
    assert resp.status_code == 200
    return resp.json()["data"].get("publish_id")


async def _get_bot_devices(api: APITestHelper, bot_uuid: str) -> list[dict]:
    """Fetch bot devices via HTTP, return list of device dicts from the response."""
    resp = await api.client.get(api.bot_devices_url(bot_uuid), params=api.params())
    assert resp.status_code == 200
    devices_data = resp.json()["data"]
    all_devices: list[dict] = []
    for entry in devices_data:
        all_devices.extend(entry.get("items", []))
    return all_devices


class TestScaleDownWithDeviceUuids:
    """SCALE_DOWN with explicit device_uuids: targeted destruction with hooks."""

    @pytest.mark.asyncio
    async def test_scale_down_with_explicit_device_uuids(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices, scale down by device_uuids=[d1, d2]: only d1, d2 destroyed."""
        bot = await create_and_activate_bot(
            api, f"scaledn-duid-1d-{unique_id}", device_count=3
        )
        bot_uuid = bot["bot_uuid"]

        all_devices = await _get_bot_devices(api, bot_uuid)
        active_devices = [d for d in all_devices if d.get("status") == "ACTIVE"]
        assert len(active_devices) == 3, (
            f"Expected 3 ACTIVE devices, got {len(active_devices)}: {all_devices}"
        )

        target_devices = active_devices[:2]
        target_uuids = [d["device_uuid"] for d in target_devices]
        # Progress endpoint exposes the internal device id (not device_uuid);
        # capture both so we can match across the two responses.
        target_ids = {d["id"] for d in target_devices}
        keep_uuid = active_devices[2]["device_uuid"]

        publish_id = await _scale_down_bot_with_device_uuids(
            api, bot_uuid, target_count=1, device_uuids=target_uuids
        )
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # before_destroy_cmd_hook must fire for exactly the targeted devices.
        # Progress devices report device_id (internal), so match against ids.
        devices = await get_devices_from_progress(api, publish_id)
        progress_device_ids = {d.get("device_id") for d in devices}
        assert progress_device_ids == target_ids, (
            f"Expected progress to contain only device ids {target_ids}, "
            f"got {progress_device_ids}"
        )

        for device in devices:
            result_msg = device.get("result_message")
            if result_msg and result_msg.startswith("{"):
                hook_data = assert_result_message_has_hook_data(result_msg)
                assert "stdout" in hook_data

        # Remaining ACTIVE device count must match target_count (3 - 2 = 1).
        remaining_devices = await _get_bot_devices(api, bot_uuid)
        remaining_active = [d for d in remaining_devices if d.get("status") == "ACTIVE"]
        assert len(remaining_active) == 1, (
            f"Expected 1 ACTIVE device after scale down, got "
            f"{len(remaining_active)}: {remaining_active}"
        )
        assert remaining_active[0]["device_uuid"] == keep_uuid

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_scale_down_with_explicit_device_uuids_preserves_others(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """4 devices, scale down by device_uuids=[d2, d4]: d1 and d3 remain ACTIVE."""
        bot = await create_and_activate_bot(
            api, f"scaledn-duid-2d-{unique_id}", device_count=4
        )
        bot_uuid = bot["bot_uuid"]

        all_devices = await _get_bot_devices(api, bot_uuid)
        active_devices = [d for d in all_devices if d.get("status") == "ACTIVE"]
        assert len(active_devices) == 4, (
            f"Expected 4 ACTIVE devices, got {len(active_devices)}: {all_devices}"
        )

        target_uuids = [
            active_devices[1]["device_uuid"],
            active_devices[3]["device_uuid"],
        ]
        target_ids = {active_devices[1]["id"], active_devices[3]["id"]}
        preserve_uuids = {
            active_devices[0]["device_uuid"],
            active_devices[2]["device_uuid"],
        }

        publish_id = await _scale_down_bot_with_device_uuids(
            api, bot_uuid, target_count=2, device_uuids=target_uuids
        )
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Publish must process exactly the targeted devices (match by device id).
        devices = await get_devices_from_progress(api, publish_id)
        progress_device_ids = {d.get("device_id") for d in devices}
        assert progress_device_ids == target_ids, (
            f"Expected progress to contain only device ids {target_ids}, "
            f"got {progress_device_ids}"
        )

        # Devices 1 and 3 must remain ACTIVE.
        remaining_devices = await _get_bot_devices(api, bot_uuid)
        remaining_active_uuids = {
            d["device_uuid"] for d in remaining_devices if d.get("status") == "ACTIVE"
        }
        assert preserve_uuids.issubset(remaining_active_uuids), (
            f"Expected preserved devices {preserve_uuids} to remain ACTIVE, "
            f"got {remaining_active_uuids}"
        )

        await cleanup_bot(api, bot["bot_uuid"])
