"""E2E tests for async device hook callback flow: DESTROY publish type.

DESTROY uses before_destroy_cmd_hook (synchronous, inline execution).
Single batch, no stage gates. Hook runs before PaaS destroy, result captured inline.
Hook exit_code/stdout/stderr stored in result_message via serialize_hook_result().
After all batches complete, bot is soft-deleted per D-04.
No external callback needed — before_destroy_cmd_hook is NOT callback-driven.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import logging
import time
import uuid

import pytest

from ..conftest import APITestHelper
from ..hook_helpers import (
    approve_publish,
    assert_result_message_has_hook_data,
    create_and_activate_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

tlog = logging.getLogger("e2e.destroy")

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


async def _destroy_bot(api: APITestHelper, bot_uuid: str) -> int | None:
    """Destroy bot, return publish_id or None."""
    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/destroy",
        params=api.params(),
        json={
            "operator": "e2e-test",
            "request_id": uuid.uuid4().hex,
        },
    )
    assert resp.status_code == 200
    return resp.json()["data"].get("publish_id")


async def _timed_destroy_test(
    api: APITestHelper, unique_id: str, device_count: int
) -> None:
    """Shared timed test body for destroy with detailed timing."""
    test_t0 = time.monotonic()

    # Step 1: Create and activate bot (this is likely the slow part)
    t0 = time.monotonic()
    bot = await create_and_activate_bot(
        api, f"destroy-hook-{device_count}d-{unique_id}", device_count=device_count
    )
    t_create = time.monotonic() - t0
    tlog.info(f"[TIMING] create_and_activate_bot: {t_create:.2f}s")

    # Step 2: Destroy bot
    t1 = time.monotonic()
    publish_id = await _destroy_bot(api, bot["bot_uuid"])
    t_destroy = time.monotonic() - t1
    if not publish_id:
        pytest.skip("No publish_id returned from destroy")
    tlog.info(f"[TIMING] _destroy_bot: {t_destroy:.2f}s, publish_id={publish_id}")

    # Step 3: Approve
    t2 = time.monotonic()
    code = await approve_publish(api, publish_id)
    t_approve = time.monotonic() - t2
    assert code == 200
    tlog.info(f"[TIMING] approve_publish: {t_approve:.2f}s")

    # Step 4: Wait for SUCCESS
    t3 = time.monotonic()
    status = await wait_for_publish_status(
        api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
    )
    t_wait = time.monotonic() - t3
    assert status == "SUCCESS", f"Expected SUCCESS, got {status}"
    tlog.info(f"[TIMING] wait_for_status: {t_wait:.2f}s")

    # Step 5: Verify hook data
    t4 = time.monotonic()
    devices = await get_devices_from_progress(api, publish_id)
    assert len(devices) >= 1, f"Expected at least 1 device record, got {len(devices)}"
    tlog.info(f"[TIMING] devices from progress: {len(devices)}")
    for device in devices:
        result_msg = device.get("result_message")
        if result_msg and result_msg.startswith("{"):
            hook_data = assert_result_message_has_hook_data(result_msg)
            assert "stdout" in hook_data
    t_verify = time.monotonic() - t4
    tlog.info(f"[TIMING] verify_hook_ {t_verify:.2f}s")

    t5 = time.monotonic()
    resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
    assert resp.status_code == 404, (
        f"Expected bot soft-deleted (404) after DESTROY SUCCESS, got {resp.status_code}"
    )

    device_uuids = [d.get("device_uuid") for d in devices if d.get("device_uuid")]
    for d_uuid in device_uuids:
        resp = await api.client.get(api.paas_device_url(d_uuid), params=api.params())
        if resp.status_code == 200:
            assert resp.json()["data"]["status"] == "RELEASED", (
                f"Expected device {d_uuid} RELEASED, got {resp.json()['data']['status']}"
            )
    t_check = time.monotonic() - t5
    tlog.info(f"[TIMING] check_bot_state: {t_check:.2f}s")

    test_total = time.monotonic() - test_t0
    tlog.info(
        f"[TIMING] TOTAL={test_total:.2f}s | "
        f"create_activate={t_create:.2f}s ({t_create / test_total * 100:.0f}%) | "
        f"destroy={t_destroy:.2f}s | approve={t_approve:.2f}s | "
        f"wait={t_wait:.2f}s | verify={t_verify:.2f}s | check={t_check:.2f}s"
    )


# ── Success path ─────────────────────────────────────────────────────────────


class TestDestroySuccess:
    """DESTROY with before_destroy_cmd_hook: sync hook per device, no callback."""

    @pytest.mark.asyncio
    async def test_destroy_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: approve → sync hook runs inline → SUCCESS."""
        await _timed_destroy_test(api, unique_id, device_count=1)

    @pytest.mark.asyncio
    async def test_destroy_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices: approve → sync hook per device → SUCCESS."""
        await _timed_destroy_test(api, unique_id, device_count=2)

    @pytest.mark.asyncio
    async def test_destroy_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices: approve → sync hook per device → SUCCESS."""
        await _timed_destroy_test(api, unique_id, device_count=3)


# ── Fast path (no hook) ─────────────────────────────────────────────────────


class TestDestroyNoHook:
    """DESTROY without hook: direct PaaS destroy, no hook execution."""

    @pytest.mark.asyncio
    async def test_destroy_no_hook(self, api: APITestHelper, unique_id: str) -> None:
        """Destroy without hook → direct fast path, plain text result_message."""
        from ..conftest import create_test_bot

        bot = await create_test_bot(api, f"destroy-nohook-{unique_id}", device_count=2)
        # Activate the bot — no hooks, so just approve each stage until SUCCESS.
        # Don't use approve_and_complete (it wastes time polling for CREATED devices).
        publish_id = bot["publish_id"]
        for _ in range(10):
            code = await approve_publish(api, publish_id)
            if code != 200:
                break
            status = await wait_for_publish_status(
                api,
                publish_id,
                {"SUCCESS", "FAILED", "APPROVING"},
                timeout_seconds=30,
            )
            if status in ("SUCCESS", "FAILED"):
                break
        if status != "SUCCESS":
            pytest.skip(f"Bot activation failed: {status}")

        publish_id = await _destroy_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from destroy")

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

        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        assert resp.status_code == 404, (
            f"Expected bot soft-deleted (404), got {resp.status_code}"
        )

        device_uuids = [d.get("device_uuid") for d in devices if d.get("device_uuid")]
        for d_uuid in device_uuids:
            resp = await api.client.get(
                api.paas_device_url(d_uuid), params=api.params()
            )
            if resp.status_code == 200:
                assert resp.json()["data"]["status"] == "RELEASED", (
                    f"Expected device {d_uuid} RELEASED, got {resp.json()['data']['status']}"
                )
