"""E2E lifecycle test: create → scale up → scale down with device_uuids → verify.

Full flow:
1. Create bot with 2 devices
2. Scale up to 4 devices (with async hook callbacks)
3. Scale down by specifying 2 device_uuids to destroy
4. Verify final state: correct 2 devices destroyed, correct 2 remain ACTIVE

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    approve_publish,
    cleanup_bot,
    create_and_activate_bot,
    get_devices_from_progress,
    send_callbacks_for_hook_devices,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e_asgi]


# ── Helpers (inlined per existing test-file convention) ──────────────────────


async def _scale_up_bot(
    api: APITestHelper, bot_uuid: str, target_count: int
) -> int | None:
    """Scale up bot, return publish_id or None."""
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
    resp = await api.client.get(
        api.bot_devices_url(bot_uuid), params=api.params()
    )
    assert resp.status_code == 200
    devices_data = resp.json()["data"]
    all_devices: list[dict] = []
    for entry in devices_data:
        all_devices.extend(entry.get("items", []))
    return all_devices


# ── Lifecycle test ────────────────────────────────────────────────────────────


class TestScaleLifecycleWithDeviceUuids:
    """Full lifecycle: create → scale up → scale down with device_uuids."""

    @pytest.mark.asyncio
    async def test_create_scale_up_scale_down_with_device_uuids_lifecycle(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Exercise full bot scaling lifecycle with explicit device_uuids on scale down.

        Steps:
        1. Create bot with 2 devices → verify 2 ACTIVE
        2. Scale up to 4 devices (async hook callbacks) → verify 4 ACTIVE
        3. Pick the 2 devices added during scale-up as destroy targets
        4. Scale down with device_uuids=[new1, new2] → verify they are
           DESTROYED, the original 2 remain ACTIVE, total ACTIVE == 2
        5. Cleanup
        """
        # ── Step 1: Create a bot with 2 devices ──────────────────────────────
        bot = await create_and_activate_bot(
            api, f"lifecycle-duid-{unique_id}", device_count=2
        )
        bot_uuid = bot["bot_uuid"]

        all_devices = await _get_bot_devices(api, bot_uuid)
        active_devices = [d for d in all_devices if d.get("status") == "ACTIVE"]
        assert len(active_devices) == 2, (
            f"Expected 2 ACTIVE devices after create, got "
            f"{len(active_devices)}: {active_devices}"
        )

        # Capture the 2 original device UUIDs (must survive scale down later)
        original_device_uuids = {
            d["device_uuid"] for d in active_devices
        }

        # ── Step 2: Scale up from 2 to 4 devices ─────────────────────────────
        publish_id = await _scale_up_bot(api, bot_uuid, target_count=4)
        assert publish_id is not None, "Scale up must return a publish_id"

        code = await approve_publish(api, publish_id)
        assert code == 200, f"Approve scale-up publish failed: {code}"

        # SCALE_UP uses after_create_cmd_hook (async, callback-driven)
        await send_callbacks_for_hook_devices(api, publish_id)

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", (
            f"Expected scale-up publish SUCCESS, got {status}"
        )

        all_devices = await _get_bot_devices(api, bot_uuid)
        active_devices = [d for d in all_devices if d.get("status") == "ACTIVE"]
        assert len(active_devices) == 4, (
            f"Expected 4 ACTIVE devices after scale up, got "
            f"{len(active_devices)}: {active_devices}"
        )

        # ── Step 3: Identify the 2 devices added during scale-up ─────────────
        scale_up_added_uuids = {
            d["device_uuid"]
            for d in active_devices
            if d["device_uuid"] not in original_device_uuids
        }
        assert len(scale_up_added_uuids) == 2, (
            f"Expected 2 newly added devices after scale up, got "
            f"{len(scale_up_added_uuids)}: {scale_up_added_uuids} "
            f"(original={original_device_uuids})"
        )
        device_uuids_to_destroy = sorted(scale_up_added_uuids)

        # Sanity check: the surviving set must be the original two
        surviving_uuids = {
            d["device_uuid"]
            for d in active_devices
            if d["device_uuid"] in original_device_uuids
        }
        assert surviving_uuids == original_device_uuids, (
            f"Original devices {original_device_uuids} should all still be "
            f"ACTIVE before scale down, got {surviving_uuids}"
        )

        # ── Step 4: Scale down by explicit device_uuids ────────────────────────
        publish_id = await _scale_down_bot_with_device_uuids(
            api, bot_uuid, target_count=2, device_uuids=device_uuids_to_destroy
        )
        assert publish_id is not None, "Scale down must return a publish_id"

        # SCALE_DOWN uses before_destroy_cmd_hook (synchronous, inline):
        # no callback needed — the hook runs before PaaS destroy.
        code = await approve_publish(api, publish_id)
        assert code == 200, f"Approve scale-down publish failed: {code}"

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", (
            f"Expected scale-down publish SUCCESS, got {status}"
        )

        # 4a. The two targeted devices must be gone from the listing.
        # destroy_device_by_uuid soft-deletes (is_deleted=1) before setting
        # status=RELEASED, so list_by_bot_id (which filters is_deleted=0)
        # never returns them. Assert absence rather than status.
        all_devices = await _get_bot_devices(api, bot_uuid)
        all_device_uuids = {d["device_uuid"] for d in all_devices}
        assert not (set(device_uuids_to_destroy) & all_device_uuids), (
            f"Targeted devices {set(device_uuids_to_destroy)} should be "
            f"absent from the bot device listing after scale down, "
            f"got {all_device_uuids}"
        )

        # 4b. The two original devices must remain ACTIVE.
        remaining_active = [
            d for d in all_devices if d.get("status") == "ACTIVE"
        ]
        remaining_active_uuids = {d["device_uuid"] for d in remaining_active}
        assert remaining_active_uuids == original_device_uuids, (
            f"Expected original devices {original_device_uuids} to remain "
            f"ACTIVE, got {remaining_active_uuids}"
        )

        # 4c. Total ACTIVE count must be exactly 2.
        assert len(remaining_active) == 2, (
            f"Expected exactly 2 ACTIVE devices after scale down, got "
            f"{len(remaining_active)}: {remaining_active}"
        )

        # ── Step 5: Cleanup ──────────────────────────────────────────────────
        await cleanup_bot(api, bot_uuid)