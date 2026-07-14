"""E2E tests for async device hook callback flow: CREATE publish type.

CREATE uses after_create_cmd_hook (async, callback-driven).
Multi-stage pipeline: PREPUB → GRAY → PROD_FIRST_BATCH → PROD_OTHER_BATCH.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import logging
import time

import pytest

from ...conftest import APITestHelper, cleanup_bot
from ...hook_helpers import (
    approve_and_complete,
    approve_publish,
    assert_result_message_has_hook_data,
    create_hook_bot,
    get_devices_from_progress,
    send_callbacks_for_hook_devices,
    wait_for_publish_status,
)

tlog = logging.getLogger("e2e.create")

pytestmark = [pytest.mark.e2e, pytest.mark.async_hook]


# ── Success path ─────────────────────────────────────────────────────────────


class TestCreateSuccess:
    """CREATE with hook: approve → callback SUCCESS → publish SUCCESS."""

    @pytest.mark.asyncio
    async def test_create_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: approve → callback → SUCCESS."""
        t0 = time.monotonic()
        bot = await create_hook_bot(api, f"create-hook-1d-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]
        tlog.info(
            f"[TIMING] create_hook_bot: {time.monotonic() - t0:.2f}s, publish_id={publish_id}"
        )

        # 1. Approve
        t1 = time.monotonic()
        code = await approve_publish(api, publish_id)
        assert code == 200
        tlog.info(f"[TIMING] approve_publish: {time.monotonic() - t1:.2f}s")

        # 2. Send callback for dispatched hook
        t2 = time.monotonic()
        await send_callbacks_for_hook_devices(api, publish_id)
        tlog.info(f"[TIMING] send_callbacks: {time.monotonic() - t2:.2f}s")

        # 3. Verify publish succeeded
        t3 = time.monotonic()
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"
        tlog.info(f"[TIMING] wait_for_status: {time.monotonic() - t3:.2f}s")

        # 4. Verify hook data in result_message
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            result_msg = device.get("result_message")
            if result_msg:
                hook_data = assert_result_message_has_hook_data(result_msg)
                assert "stdout" in hook_data

        # 5. Verify bot and devices via detail-by-uuid
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1
        active_devices = []
        for item in items:
            active_devices.extend(
                [d for d in item["devices"] if d["status"] == "ACTIVE"]
            )
        assert len(active_devices) >= 1

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices: Auto-distribute to [PROD_FIRST_BATCH, PROD_OTHER_BATCH].

        PROD_FIRST_BATCH has pause_for_approval=True (needs approve).
        PROD_OTHER_BATCH has pause_for_approval=False (auto-continues after callback).
        So: 1 approve gate, 2 callback rounds (1 per stage).
        """
        bot = await create_hook_bot(api, f"create-hook-2d-{unique_id}", device_count=2)
        publish_id = bot["publish_id"]

        status = await approve_and_complete(api, publish_id)
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices: Auto-distribute to [GRAY, PROD_FIRST_BATCH, PROD_OTHER_BATCH].

        GRAY has pause_for_approval=True, PROD_FIRST_BATCH has pause_for_approval=True.
        PROD_OTHER_BATCH has pause_for_approval=False (auto-continues after callback).
        """
        bot = await create_hook_bot(api, f"create-hook-3d-{unique_id}", device_count=3)
        publish_id = bot["publish_id"]

        status = await approve_and_complete(api, publish_id)
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])


# ── Failure path ─────────────────────────────────────────────────────────────


class TestCreateFailure:
    """CREATE with hook: callback FAILED → publish FAILED."""

    @pytest.mark.asyncio
    async def test_create_1_device_hook_failure(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: approve → FAILED callback → publish FAILED."""
        bot = await create_hook_bot(api, f"create-fail-1d-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]

        # 1. Approve
        code = await approve_publish(api, publish_id)
        assert code == 200

        # 2. Send FAILED callback
        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=1,
            stdout="",
            stderr="hook script exited with error",
        )

        # 3. Verify publish FAILED
        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        # 4. Verify failure data in result_message
        devices = await get_devices_from_progress(api, publish_id)
        hook_data = assert_result_message_has_hook_data(
            devices[0].get("result_message"), expected_exit_code=1
        )
        assert "stderr" in hook_data
        assert "hook script exited with error" in hook_data["stderr"]

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_1_device_hook_killed(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: approve → callback with exit_code=137 → publish FAILED."""
        bot = await create_hook_bot(
            api, f"create-fail-kill-{unique_id}", device_count=1
        )
        publish_id = bot["publish_id"]

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=137,
            stderr="process killed by signal 9",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        devices = await get_devices_from_progress(api, publish_id)
        hook_data = assert_result_message_has_hook_data(
            devices[0].get("result_message"), expected_exit_code=137
        )
        assert "process killed" in hook_data.get("stderr", "")

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_2_devices_one_fails(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices: approve → FAILED callback on stage 1 → publish FAILED.

        Auto-distribute to [PROD_FIRST_BATCH, PROD_OTHER_BATCH], 1 device per stage.
        Stage 1 device fails → entire publish fails.
        """
        bot = await create_hook_bot(api, f"create-mixed-{unique_id}", device_count=2)
        publish_id = bot["publish_id"]

        code = await approve_publish(api, publish_id)
        assert code == 200

        # Fail the only device in stage 1
        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=1,
            stderr="first device failed",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_create_3_devices_one_fails_per_stage(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices: approve → FAILED callback on first stage → publish FAILED."""
        bot = await create_hook_bot(api, f"create-3d-fail-{unique_id}", device_count=3)
        publish_id = bot["publish_id"]

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=1,
            stderr="first stage failure",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])


# ── No-hook baseline ────────────────────────────────────────────────────────


class TestCreateNoHook:
    """CREATE without hook → device ACTIVE immediately (no callback needed)."""

    @pytest.mark.asyncio
    async def test_create_no_hook(self, api: APITestHelper, unique_id: str) -> None:
        from ...conftest import create_test_bot

        bot = await create_test_bot(api, f"create-nohook-{unique_id}")
        publish_id = bot["publish_id"]

        # Approve only — no callback needed for no-hook path
        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "APPROVING", "FAILED"}, timeout_seconds=0.5
        )
        assert status in ("SUCCESS", "APPROVING"), (
            f"Expected SUCCESS or APPROVING, got {status}"
        )

        await cleanup_bot(api, bot["bot_uuid"])
