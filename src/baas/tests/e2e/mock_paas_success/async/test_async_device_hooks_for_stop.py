"""E2E tests for async device hook callback flow: STOP publish type.

STOP uses before_destroy_cmd_hook (synchronous, inline execution).
Single batch, no stage gates. Hook runs before PaaS destroy, result captured inline.
After all batches complete, bot is marked STOPPED and devices are set to STOPPED.
No soft-delete — records preserved for potential restart.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import logging
import time
import uuid

import pytest

from ...conftest import APITestHelper
from ...hook_helpers import (
    approve_publish,
    assert_result_message_has_hook_data,
    create_and_activate_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

tlog = logging.getLogger("e2e.stop")

pytestmark = [pytest.mark.e2e, pytest.mark.async_hook]


async def _stop_bot(api: APITestHelper, bot_uuid: str) -> int | None:
    """Stop bot, return publish_id or None."""
    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/stop",
        params=api.params(),
        json={
            "operator": "e2e-test",
            "request_id": uuid.uuid4().hex,
        },
    )
    assert resp.status_code == 200
    return resp.json()["data"].get("publish_id")


async def _timed_stop_test(
    api: APITestHelper, unique_id: str, device_count: int
) -> None:
    """Shared timed test body for stop with detailed timing."""
    test_t0 = time.monotonic()

    # Step 1: Create and activate bot
    t0 = time.monotonic()
    bot = await create_and_activate_bot(
        api, f"stop-hook-{device_count}d-{unique_id}", device_count=device_count
    )
    t_create = time.monotonic() - t0
    tlog.info(f"[TIMING] create_and_activate_bot: {t_create:.2f}s")

    # Step 2: Stop bot
    t1 = time.monotonic()
    publish_id = await _stop_bot(api, bot["bot_uuid"])
    t_stop = time.monotonic() - t1
    if not publish_id:
        pytest.skip("No publish_id returned from stop")
    tlog.info(f"[TIMING] _stop_bot: {t_stop:.2f}s, publish_id={publish_id}")

    # Step 3: Verify bot is in STOPPING state immediately
    resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
    assert resp.status_code == 200
    status = resp.json()["data"]["status"]
    tlog.info(f"[TIMING] bot status after stop: {status}")
    assert status == "STOPPING", f"Expected STOPPING, got {status}"

    # Step 4: Approve the STOP publish
    t2 = time.monotonic()
    code = await approve_publish(api, publish_id)
    t_approve = time.monotonic() - t2
    assert code == 200
    tlog.info(f"[TIMING] approve_publish: {t_approve:.2f}s")

    # Step 5: Wait for SUCCESS
    t3 = time.monotonic()
    pstatus = await wait_for_publish_status(
        api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
    )
    t_wait = time.monotonic() - t3
    assert pstatus == "SUCCESS", f"Expected SUCCESS, got {pstatus}"
    tlog.info(f"[TIMING] wait_for_status: {t_wait:.2f}s")

    # Step 6: Verify bot status is STOPPED (not 404 — records preserved)
    t4 = time.monotonic()
    resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
    assert resp.status_code == 200, (
        f"Expected bot to exist (200) after STOP SUCCESS, got {resp.status_code}"
    )
    assert resp.json()["data"]["status"] == "STOPPED", (
        f"Expected bot STOPPED, got {resp.json()['data']['status']}"
    )
    tlog.info(f"[TIMING] verify_bot_stopped: {time.monotonic() - t4:.2f}s")

    # Step 7: Verify devices are STOPPED (not RELEASED — records preserved)
    devices = await get_devices_from_progress(api, publish_id)
    assert len(devices) >= 1, f"Expected at least 1 device record, got {len(devices)}"
    device_uuids = [d.get("device_uuid") for d in devices if d.get("device_uuid")]
    for d_uuid in device_uuids:
        resp = await api.client.get(api.paas_device_url(d_uuid), params=api.params())
        if resp.status_code == 200:
            assert resp.json()["data"]["status"] == "STOPPED", (
                f"Expected device {d_uuid} STOPPED, got {resp.json()['data']['status']}"
            )
    t_check = time.monotonic() - t4
    tlog.info(f"[TIMING] check_devices_stopped: {t_check:.2f}s")

    test_total = time.monotonic() - test_t0
    tlog.info(
        f"[TIMING] TOTAL={test_total:.2f}s | "
        f"create_activate={t_create:.2f}s | stop={t_stop:.2f}s | "
        f"approve={t_approve:.2f}s | wait={t_wait:.2f}s | "
        f"verify={t_check:.2f}s"
    )


class TestStopSuccess:
    """STOP with before_destroy_cmd_hook: sync hook per device, no callback."""

    @pytest.mark.asyncio
    async def test_stop_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: approve -> sync hook runs inline -> SUCCESS."""
        await _timed_stop_test(api, unique_id, device_count=1)

    @pytest.mark.asyncio
    async def test_stop_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices: approve -> sync hook per device -> SUCCESS."""
        await _timed_stop_test(api, unique_id, device_count=2)

    @pytest.mark.asyncio
    async def test_stop_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices: approve -> sync hook per device -> SUCCESS."""
        await _timed_stop_test(api, unique_id, device_count=3)


# ── Fast path (no hook) ─────────────────────────────────────────────────────


class TestStopNoHook:
    """STOP without hook: direct PaaS destroy, no hook execution."""

    @pytest.mark.asyncio
    async def test_stop_no_hook(self, api: APITestHelper, unique_id: str) -> None:
        """Stop without hook -> direct fast path, plain text result_message."""
        from ...conftest import create_test_bot

        bot = await create_test_bot(api, f"stop-nohook-{unique_id}", device_count=2)
        # Activate the bot — no hooks, so just approve each stage until SUCCESS.
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

        # Stop the bot
        publish_id = await _stop_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from stop")

        code = await approve_publish(api, publish_id)
        assert code == 200

        pstatus = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert pstatus == "SUCCESS", f"Expected SUCCESS, got {pstatus}"

        # Verify bot status is STOPPED (not 404)
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        assert resp.status_code == 200, (
            f"Expected bot to exist (200) after STOP SUCCESS, got {resp.status_code}"
        )
        assert resp.json()["data"]["status"] == "STOPPED", (
            f"Expected bot STOPPED, got {resp.json()['data']['status']}"
        )

        # Verify devices are STOPPED (not RELEASED)
        devices = await get_devices_from_progress(api, publish_id)
        device_uuids = [d.get("device_uuid") for d in devices if d.get("device_uuid")]
        for d_uuid in device_uuids:
            resp = await api.client.get(
                api.paas_device_url(d_uuid), params=api.params()
            )
            if resp.status_code == 200:
                assert resp.json()["data"]["status"] == "STOPPED", (
                    f"Expected device {d_uuid} STOPPED, got {resp.json()['data']['status']}"
                )


# ── Stop then Restart / Update ──────────────────────────────────────────────


class TestStopThenOperate:
    """STOP then restart/update — verify eligibility and record statuses."""

    async def _verify_stopped(self, api, bot_uuid):
        """Verify bot is STOPPED with devices in STOPPED status."""
        resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "STOPPED", (
            f"Expected STOPPED, got {resp.json()['data']['status']}"
        )

        # Check devices via detail-by-uuid
        resp = await api.client.get(
            f"{api.bot_url(bot_uuid)}/detail-by-uuid", params=api.params()
        )
        assert resp.status_code == 200
        for item in resp.json()["data"].get("items", []):
            for device in item.get("devices", []):
                assert device["status"] == "STOPPED", (
                    f"Expected device STOPPED, got {device['status']}"
                )

    @pytest.mark.asyncio
    async def test_restart_after_stop(self, api: APITestHelper, unique_id: str) -> None:
        from ...conftest import cleanup_bot, create_test_bot

        bot = await create_test_bot(api, f"stop-restart-{unique_id}", device_count=1)
        bot_uuid = bot["bot_uuid"]

        pid = bot["publish_id"]
        for _ in range(10):
            code = await approve_publish(api, pid)
            if code != 200:
                break
            status = await wait_for_publish_status(
                api, pid, {"SUCCESS", "FAILED", "APPROVING"}, timeout_seconds=30
            )
            if status in ("SUCCESS", "FAILED"):
                break

        # Stop and verify
        spid = await _stop_bot(api, bot_uuid)
        if not spid:
            pytest.skip("No publish_id from stop")
        code = await approve_publish(api, spid)
        assert code == 200
        status = await wait_for_publish_status(
            api, spid, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS"
        await self._verify_stopped(api, bot_uuid)

        # Restart should not be rejected by STOPPED status
        resp = await api.client.post(
            api.bot_url(bot_uuid) + "/restart",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "scope": "all",
            },
        )
        assert resp.status_code in (200, 409), (
            f"restart_bot should not reject STOPPED, got {resp.status_code}: {resp.text}"
        )

        await cleanup_bot(api, bot_uuid)

    @pytest.mark.asyncio
    async def test_update_after_stop(self, api: APITestHelper, unique_id: str) -> None:
        from ...conftest import cleanup_bot, create_test_bot

        bot = await create_test_bot(api, f"stop-update-{unique_id}", device_count=1)
        bot_uuid = bot["bot_uuid"]

        pid = bot["publish_id"]
        for _ in range(10):
            code = await approve_publish(api, pid)
            if code != 200:
                break
            status = await wait_for_publish_status(
                api, pid, {"SUCCESS", "FAILED", "APPROVING"}, timeout_seconds=30
            )
            if status in ("SUCCESS", "FAILED"):
                break

        # Stop and verify device records are STOPPED and preserved
        spid = await _stop_bot(api, bot_uuid)
        if not spid:
            pytest.skip("No publish_id from stop")
        code = await approve_publish(api, spid)
        assert code == 200
        status = await wait_for_publish_status(
            api, spid, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS"
        await self._verify_stopped(api, bot_uuid)

        # Name-only update should not be rejected by STOPPED status
        resp = await api.client.post(
            api.bot_url(bot_uuid) + "/update",
            params=api.params(),
            json={
                "name": f"updated-after-stop-{unique_id}",
                "operator": "e2e-test",
            },
        )
        assert resp.status_code in (200, 409), (
            f"update_bot should not reject STOPPED, got {resp.status_code}: {resp.text}"
        )

        await cleanup_bot(api, bot_uuid)
