"""E2E tests for async device hook callback timeout.

Tests that publish records stuck in CREATED beyond callback_timeout_seconds
are automatically transitioned to FAILED via synthetic callbacks when
get_publish_progress is polled.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import asyncio
import json
import logging

import pytest

from ..conftest import APITestHelper, cleanup_bot
from ..hook_helpers import (
    approve_publish,
    create_hook_bot,
    get_devices_from_progress,
    get_publish_status,
    wait_for_publish_status,
)

log = logging.getLogger("e2e.timeout")

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

# Short timeout for tests — real default is 600s, but we use enough margin
# to avoid flaky timeout firing before the test can confirm PROCESSING state.
# The stale check uses gmt_create (not gmt_modified), so total elapsed time
# from device record creation must stay well under this value.
TEST_CALLBACK_TIMEOUT = 5


class TestCallbackTimeout:
    """Publish records in CREATED past callback_timeout_seconds → FAILED via progress poll."""

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="Known issue: progress endpoint returns 404 in timeout flow"
    )
    async def test_1_device_timeout_marks_failed(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: approve → no callback → poll progress after timeout → FAILED."""
        bot = await create_hook_bot(
            api,
            f"timeout-1d-{unique_id}",
            device_count=1,
            callback_timeout_seconds=TEST_CALLBACK_TIMEOUT,
        )
        publish_id = bot["publish_id"]

        # 1. Approve — dispatches hook, record stays CREATED
        code = await approve_publish(api, publish_id)
        assert code == 200

        # 2. Poll until device reaches PROCESSING state (with timeout)
        #    Using poll instead of fixed sleep avoids races with mock PaaS startup
        devices = []
        created_devices = []
        for _ in range(20):
            devices = await get_devices_from_progress(api, publish_id)
            created_devices = [
                d for d in devices if d.get("result_status") == "PROCESSING"
            ]
            if created_devices:
                break
            await asyncio.sleep(0.25)

        # 3. Confirm device is in PROCESSING state (no callback sent yet)
        assert len(created_devices) >= 1, (
            f"Expected at least 1 PROCESSING device, got: {[d.get('result_status') for d in devices]}"
        )

        # 4. Wait for timeout to elapse (+ buffer)
        log.info(
            f"[TIMEOUT] Waiting {TEST_CALLBACK_TIMEOUT + 1}s for callback timeout to fire..."
        )
        await asyncio.sleep(TEST_CALLBACK_TIMEOUT + 1)

        # 5. Poll progress — this triggers _check_and_handle_timeout
        resp = await api.client.get(
            api.publish_url(publish_id, "progress"),
            params=api.params(),
        )
        assert resp.status_code == 200

        # 6. Verify publish transitioned to FAILED
        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=10
        )
        assert status == "FAILED", f"Expected FAILED after timeout, got {status}"

        # 7. Verify device record shows FAILED with timeout message
        devices = await get_devices_from_progress(api, publish_id)
        timed_out = [d for d in devices if d.get("result_status") == "FAILED"]
        assert len(timed_out) >= 1, (
            f"Expected at least 1 FAILED device after timeout, got: {[d.get('result_status') for d in devices]}"
        )

        # 8. Verify timeout indicator in result_message
        for device in timed_out:
            result_msg = device.get("result_message")
            if result_msg:
                hook_data = json.loads(result_msg)
                assert hook_data.get("exit_code") == -1, (
                    f"Expected exit_code=-1 for timeout, got {hook_data.get('exit_code')}"
                )
                assert "timeout" in hook_data.get("stderr", "").lower(), (
                    f"Expected 'timeout' in stderr, got: {hook_data.get('stderr')}"
                )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="Known issue: timeout flow incomplete, depends on test_1_device_timeout_marks_failed fix"
    )
    async def test_timeout_then_late_callback_ignored(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """After timeout marks record FAILED, a late real callback is ignored."""
        bot = await create_hook_bot(
            api,
            f"timeout-late-cb-{unique_id}",
            device_count=1,
            callback_timeout_seconds=TEST_CALLBACK_TIMEOUT,
        )
        publish_id = bot["publish_id"]

        # 1. Approve
        code = await approve_publish(api, publish_id)
        assert code == 200

        # 2. Wait for timeout
        await asyncio.sleep(TEST_CALLBACK_TIMEOUT + 1)

        # 3. Poll progress to trigger timeout
        resp = await api.client.get(
            api.publish_url(publish_id, "progress"),
            params=api.params(),
        )
        assert resp.status_code == 200

        # 4. Confirm FAILED
        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=10
        )
        assert status == "FAILED"

        # 5. Send a late SUCCESS callback (simulates late hook response)
        devices = await get_devices_from_progress(api, publish_id)
        failed_devices = [
            d
            for d in devices
            if d.get("result_status") == "FAILED" and d.get("device_uuid")
        ]
        if failed_devices:
            from ..conftest import call_device_callback

            device = failed_devices[0]
            resp = await call_device_callback(
                api,
                device_uuid=device["device_uuid"],
                publish_id=publish_id,
                event_type="start",
                result_status="SUCCESS",
                exit_code=0,
                stdout="late response",
            )
            # Callback should be accepted (HTTP 200) but record stays FAILED (idempotency)
            assert resp.status_code == 200

        # 6. Verify publish still FAILED (not overwritten by late callback)
        await asyncio.sleep(1)
        status = await get_publish_status(api, publish_id)
        assert status == "FAILED", (
            f"Late callback should not change FAILED status, got {status}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_no_timeout_when_callback_arrives_in_time(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Callback arriving before timeout → no timeout triggered → SUCCESS."""
        bot = await create_hook_bot(
            api,
            f"timeout-no-fire-{unique_id}",
            device_count=1,
            callback_timeout_seconds=TEST_CALLBACK_TIMEOUT,
        )
        publish_id = bot["publish_id"]

        # 1. Approve
        code = await approve_publish(api, publish_id)
        assert code == 200

        # 2. Send SUCCESS callback immediately (well within timeout)
        from ..hook_helpers import send_callbacks_for_hook_devices

        await send_callbacks_for_hook_devices(api, publish_id, result_status="SUCCESS")

        # 3. Verify publish succeeded (not timed out)
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=10
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # 4. Verify devices are SUCCESS, not FAILED
        devices = await get_devices_from_progress(api, publish_id)
        success_devices = [d for d in devices if d.get("result_status") == "SUCCESS"]
        assert len(success_devices) >= 1, (
            f"Expected SUCCESS devices, got: {[d.get('result_status') for d in devices]}"
        )

        await cleanup_bot(api, bot["bot_uuid"])
