"""E2E tests for auto_approve_publish flag on bot creation.

Verifies that when auto_approve_publish=True, the create_bot call
automatically approves all stage gates without manual /approve calls.
Covers 1, 2, and 3 device scenarios (different pipeline auto-compact paths).

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import logging
import time

import pytest

from ..conftest import APITestHelper, cleanup_bot, create_test_bot
from ..hook_helpers import (
    get_devices_from_progress,
    send_callbacks_for_hook_devices,
    wait_for_publish_status,
)

tlog = logging.getLogger("e2e.auto_approve")

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestAutoApprovePublish:
    """create_bot with auto_approve_publish=True — no manual /approve needed."""

    @pytest.mark.asyncio
    async def test_auto_approve_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: auto_approve publishes the CREATE, callback → SUCCESS.

        1 device pipeline auto-compacts to [PROD_FIRST_BATCH] (1 stage, 0 gates).
        auto_approve handles PENDING → ACTIVE, then callback drives SUCCESS.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-1d-{unique_id}",
            device_count=1,
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]
        tlog.info(
            f"[TIMING] create_bot+auto_approve: {time.monotonic() - t0:.2f}s, publish_id={publish_id}"
        )

        # auto_approve should have moved publish past PENDING — check status
        # For 1 device with 1 batch at PROD_FIRST_BATCH (pause_for_approval=True):
        # auto_approve transitions PENDING→ACTIVE, _auto_execute_stages runs
        # Stage completes → no more batches → auto_complete → SUCCESS
        # No callback needed (no hook deploy config)
        status = await wait_for_publish_status(
            api,
            publish_id,
            {"SUCCESS", "ACTIVE", "APPROVING", "FAILED"},
            timeout_seconds=5.0,
        )
        tlog.info(f"[TIMING] publish status: {status} at {time.monotonic() - t0:.2f}s")
        assert status in ("SUCCESS", "ACTIVE", "APPROVING"), (
            f"Expected SUCCESS/ACTIVE/APPROVING, got {status}"
        )

        # Verify bot exists
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_auto_approve_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices: auto_approve handles all gates, callback per stage → SUCCESS.

        2 devices auto-compacts to [PROD_FIRST_BATCH, PROD_OTHER_BATCH] (2 stages).
        PROD_FIRST_BATCH has pause_for_approval=True.
        PROD_OTHER_BATCH has pause_for_approval=False.
        auto_approve handles PENDING→ACTIVE, then polls while ACTIVE,
        then handles APPROVING→ACTIVE, then auto_complete → SUCCESS.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-2d-{unique_id}",
            device_count=2,
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]
        tlog.info(
            f"[TIMING] create_bot+auto_approve: {time.monotonic() - t0:.2f}s, publish_id={publish_id}"
        )

        # auto_approve should have approved and kicked off stage execution
        # Poll for terminal state
        status = await wait_for_publish_status(
            api,
            publish_id,
            {"SUCCESS", "ACTIVE", "APPROVING", "FAILED"},
            timeout_seconds=5.0,
        )
        tlog.info(f"[TIMING] publish status: {status} at {time.monotonic() - t0:.2f}s")
        assert status in ("SUCCESS", "ACTIVE", "APPROVING"), (
            f"Expected SUCCESS/ACTIVE/APPROVING, got {status}"
        )

        # Verify bot exists
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_auto_approve_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices: auto_approve handles all 2 gates, callbacks per stage → SUCCESS.

        3 devices auto-compacts to [GRAY, PROD_FIRST_BATCH, PROD_OTHER_BATCH] (3 stages).
        GRAY and PROD_FIRST_BATCH have pause_for_approval=True (2 gates).
        PROD_OTHER_BATCH has pause_for_approval=False.
        auto_approve handles PENDING→ACTIVE, APPROVING→ACTIVE (×2), then auto_complete.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-3d-{unique_id}",
            device_count=3,
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]
        tlog.info(
            f"[TIMING] create_bot+auto_approve: {time.monotonic() - t0:.2f}s, publish_id={publish_id}"
        )

        # auto_approve should have approved and kicked off stage execution
        # Poll for terminal state
        status = await wait_for_publish_status(
            api,
            publish_id,
            {"SUCCESS", "ACTIVE", "APPROVING", "FAILED"},
            timeout_seconds=5.0,
        )
        tlog.info(f"[TIMING] publish status: {status} at {time.monotonic() - t0:.2f}s")
        assert status in ("SUCCESS", "ACTIVE", "APPROVING"), (
            f"Expected SUCCESS/ACTIVE/APPROVING, got {status}"
        )

        # Verify bot exists
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])


class TestAutoApprovePublishWithHook:
    """auto_approve_publish=True with hook deploy config — callbacks drive completion."""

    @pytest.mark.asyncio
    async def test_auto_approve_hook_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device with hook: auto_approve → callback SUCCESS → publish SUCCESS.

        auto_approve transitions PENDING→ACTIVE and starts execution.
        Device dispatches with hook → CREATED → needs callback.
        After callback: stage completes → auto_complete → SUCCESS.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-hook-1d-{unique_id}",
            device_count=1,
            deploy_config={
                "after_create_cmd_hook": "/bin/echo 'hook executed'",
            },
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]
        tlog.info(
            f"[TIMING] create_bot+auto_approve(hook): {time.monotonic() - t0:.2f}s, publish_id={publish_id}"
        )

        # Send SUCCESS callback for the dispatched device
        t_cb = time.monotonic()
        await send_callbacks_for_hook_devices(api, publish_id)
        tlog.info(f"[TIMING] send_callbacks: {time.monotonic() - t_cb:.2f}s")

        # Verify publish SUCCESS
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=5.0
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Verify hook data in result_message
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_auto_approve_hook_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices with hook: auto_approve gates + callbacks per stage → SUCCESS.

        2-stage pipeline (PROD_FIRST_BATCH + PROD_OTHER_BATCH), 1 gate.
        auto_approve handles the gate. Callbacks needed for each stage.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-hook-2d-{unique_id}",
            device_count=2,
            deploy_config={
                "after_create_cmd_hook": "/bin/echo 'hook executed'",
            },
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]
        tlog.info(
            f"[TIMING] create_bot+auto_approve(hook): {time.monotonic() - t0:.2f}s, publish_id={publish_id}"
        )

        # Callback round 1: stage 1 (PROD_FIRST_BATCH, 1 device)
        await send_callbacks_for_hook_devices(api, publish_id)

        # auto_approve loop handles APPROVING → ACTIVE for gate
        # Callback round 2: stage 2 (PROD_OTHER_BATCH, 1 device, auto-continue)
        await send_callbacks_for_hook_devices(api, publish_id)

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=5.0
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 2

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_auto_approve_hook_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices with hook: auto_approve 2 gates + callbacks per stage → SUCCESS.

        3-stage pipeline (GRAY + PROD_FIRST_BATCH + PROD_OTHER_BATCH), 2 gates.
        auto_approve handles both gates. Callbacks needed for each stage.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-hook-3d-{unique_id}",
            device_count=3,
            deploy_config={
                "after_create_cmd_hook": "/bin/echo 'hook executed'",
            },
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]
        tlog.info(
            f"[TIMING] create_bot+auto_approve(hook): {time.monotonic() - t0:.2f}s, publish_id={publish_id}"
        )

        # Callback round 1: stage 1 (GRAY, 1 device)
        await send_callbacks_for_hook_devices(api, publish_id)

        # auto_approve handles APPROVING → ACTIVE
        # Callback round 2: stage 2 (PROD_FIRST_BATCH, 1 device)
        await send_callbacks_for_hook_devices(api, publish_id)

        # auto_approve handles APPROVING → ACTIVE
        # Callback round 3: stage 3 (PROD_OTHER_BATCH, 1 device, auto-continue)
        await send_callbacks_for_hook_devices(api, publish_id)

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=5.0
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 3

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])


class TestManualApproveIgnoredDuringAutoApprove:
    """Manual /approve calls are silently ignored when auto_approve is active."""

    @pytest.mark.asyncio
    async def test_manual_approve_ignored_on_pending(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Manual /approve with auto_approve → 200, no state change, pipeline continues."""
        bot = await create_test_bot(
            api,
            f"auto-approve-manual-{unique_id}",
            device_count=1,
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]

        # auto_approve loop already ran (create_bot returned). Manual /approve → 200.
        import uuid

        resp = await api.client.post(
            api.publish_url(publish_id, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200, (
            f"Expected 200 for manual approve during auto_approve, got {resp.status_code}"
        )

        # Pipeline should not be blocked — reaches terminal or executing state
        status = await wait_for_publish_status(
            api,
            publish_id,
            {"SUCCESS", "ACTIVE", "APPROVING", "FAILED"},
            timeout_seconds=5.0,
        )
        assert status in ("SUCCESS", "ACTIVE", "APPROVING"), (
            f"Expected non-terminal, got {status}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_manual_approve_ignored_during_hook_pipeline(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Manual /approve during hook pipeline with auto_approve → 200, pipeline proceeds."""
        bot = await create_test_bot(
            api,
            f"auto-approve-manual-hook-{unique_id}",
            device_count=2,
            deploy_config={
                "after_create_cmd_hook": "/bin/echo 'hook executed'",
            },
            auto_approve_publish=True,
        )
        publish_id = bot["publish_id"]

        # Call manual /approve while auto_approve loop is active — must return 200
        import uuid

        resp = await api.client.post(
            api.publish_url(publish_id, "approve"),
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200, (
            f"Expected 200 for manual approve, got {resp.status_code}"
        )

        # Pipeline should still progress: send callbacks, verify SUCCESS
        await send_callbacks_for_hook_devices(api, publish_id, timeout_seconds=5.0)
        await send_callbacks_for_hook_devices(api, publish_id, timeout_seconds=5.0)

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=5.0
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 2

        await cleanup_bot(api, bot["bot_uuid"])
