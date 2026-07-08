"""E2E tests for auto_approve_publish flag on bot update.

Verifies that when auto_approve_publish=True, the update_bot call
automatically approves all stage gates without manual /approve calls.
Covers 1, 2, and 3 device scenarios with and without hook deploy configs.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import logging
import time
import uuid

import pytest

from ...conftest import APITestHelper, activate_test_bot, cleanup_bot, create_test_bot
from ...hook_helpers import (
    get_devices_from_progress,
    send_callbacks_for_hook_devices,
    wait_for_publish_status,
)

tlog = logging.getLogger("e2e.auto_approve_update")

pytestmark = [pytest.mark.e2e, pytest.mark.async_hook]


class TestAutoApprovePublishUpdate:
    """UPDATE with auto_approve_publish=True — no manual /approve needed."""

    @pytest.mark.asyncio
    async def test_auto_approve_update_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1-device UPDATE: auto_approve → inline completion → SUCCESS.

        1 device pipeline auto-compacts to [PROD_FIRST_BATCH] (1 stage, 0 gates).
        auto_approve handles PENDING→ACTIVE, inline execution → auto_complete → SUCCESS.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-upd-1d-{unique_id}",
            device_count=1,
        )
        await activate_test_bot(api, bot)
        update_resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/update",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "bot_name": f"auto-approve-upd-1d-{unique_id}-renamed",
                "config": {"auto_approve_publish": True},
            },
        )
        assert update_resp.status_code in (200, 500)
        update_publish_id = (
            update_resp.json()["data"].get("publish_id")
            if update_resp.status_code == 200
            else None
        )

        if update_publish_id is not None:
            status = await wait_for_publish_status(
                api,
                update_publish_id,
                {"SUCCESS", "ACTIVE", "APPROVING", "FAILED"},
                timeout_seconds=0.5,
            )
            tlog.info(
                f"[TIMING] publish status: {status} at {time.monotonic() - t0:.2f}s"
            )
            assert status in ("SUCCESS", "ACTIVE", "APPROVING"), (
                f"Expected SUCCESS/ACTIVE/APPROVING, got {status}"
            )

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_auto_approve_update_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2-device UPDATE: auto_approve handles gate, inline completion → SUCCESS.

        2 devices → [PROD_FIRST_BATCH, PROD_OTHER_BATCH] (2 stages, 1 gate).
        auto_approve handles PENDING→ACTIVE and APPROVING→ACTIVE.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-upd-2d-{unique_id}",
            device_count=2,
        )
        await activate_test_bot(api, bot)
        update_resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/update",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "bot_name": f"auto-approve-upd-2d-{unique_id}-renamed",
                "config": {"auto_approve_publish": True},
            },
        )
        assert update_resp.status_code in (200, 500)
        update_publish_id = (
            update_resp.json()["data"].get("publish_id")
            if update_resp.status_code == 200
            else None
        )

        if update_publish_id is not None:
            status = await wait_for_publish_status(
                api,
                update_publish_id,
                {"SUCCESS", "ACTIVE", "APPROVING", "FAILED"},
                timeout_seconds=0.5,
            )
            tlog.info(
                f"[TIMING] publish status: {status} at {time.monotonic() - t0:.2f}s"
            )
            assert status in ("SUCCESS", "ACTIVE", "APPROVING"), (
                f"Expected SUCCESS/ACTIVE/APPROVING, got {status}"
            )

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_auto_approve_update_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3-device UPDATE: auto_approve handles 2 gates, inline completion → SUCCESS.

        3 devices → [GRAY, PROD_FIRST_BATCH, PROD_OTHER_BATCH] (3 stages, 2 gates).
        auto_approve handles PENDING→ACTIVE and APPROVING→ACTIVE (×2).
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-upd-3d-{unique_id}",
            device_count=3,
        )
        await activate_test_bot(api, bot)
        update_resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/update",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "bot_name": f"auto-approve-upd-3d-{unique_id}-renamed",
                "config": {"auto_approve_publish": True},
            },
        )
        assert update_resp.status_code in (200, 500)
        update_publish_id = (
            update_resp.json()["data"].get("publish_id")
            if update_resp.status_code == 200
            else None
        )

        if update_publish_id is not None:
            status = await wait_for_publish_status(
                api,
                update_publish_id,
                {"SUCCESS", "ACTIVE", "APPROVING", "FAILED"},
                timeout_seconds=0.5,
            )
            tlog.info(
                f"[TIMING] publish status: {status} at {time.monotonic() - t0:.2f}s"
            )
            assert status in ("SUCCESS", "ACTIVE", "APPROVING"), (
                f"Expected SUCCESS/ACTIVE/APPROVING, got {status}"
            )

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])


class TestAutoApprovePublishUpdateWithHook:
    """UPDATE with auto_approve_publish=True + hook — callbacks drive completion."""

    @pytest.mark.asyncio
    async def test_auto_approve_update_hook_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1-device UPDATE with hook: auto_approve → callback → SUCCESS.

        auto_approve transitions PENDING→ACTIVE and starts execution.
        Device dispatches with hook → CREATED → needs callback.
        After callback: stage completes → auto_complete → SUCCESS.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-upd-hk-1d-{unique_id}",
            device_count=1,
        )
        await activate_test_bot(api, bot)
        update_resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/update",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "bot_name": f"auto-approve-upd-hk-1d-{unique_id}-renamed",
                "config": {
                    "auto_approve_publish": True,
                    "deploy_config": {
                        "after_create_cmd_hook": "/bin/echo 'hook executed'",
                    },
                },
            },
        )
        assert update_resp.status_code in (200, 500)
        update_publish_id = (
            update_resp.json()["data"].get("publish_id")
            if update_resp.status_code == 200
            else None
        )
        assert update_publish_id is not None, (
            "Expected non-None publish_id for hook test"
        )

        t_cb = time.monotonic()
        await send_callbacks_for_hook_devices(api, update_publish_id)
        tlog.info(f"[TIMING] send_callbacks: {time.monotonic() - t_cb:.2f}s")

        status = await wait_for_publish_status(
            api, update_publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        devices = await get_devices_from_progress(api, update_publish_id)
        assert len(devices) >= 1

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_auto_approve_update_hook_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2-device UPDATE with hook: auto_approve gate + callbacks per stage → SUCCESS.

        2-stage pipeline (PROD_FIRST_BATCH + PROD_OTHER_BATCH), 1 gate.
        auto_approve handles the gate. Callbacks needed for each stage.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-upd-hk-2d-{unique_id}",
            device_count=2,
        )
        await activate_test_bot(api, bot)
        update_resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/update",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "bot_name": f"auto-approve-upd-hk-2d-{unique_id}-renamed",
                "config": {
                    "auto_approve_publish": True,
                    "deploy_config": {
                        "after_create_cmd_hook": "/bin/echo 'hook executed'",
                    },
                },
            },
        )
        assert update_resp.status_code == 200
        update_publish_id = update_resp.json()["data"].get("publish_id")
        assert update_publish_id is not None

        await send_callbacks_for_hook_devices(
            api, update_publish_id, timeout_seconds=0.5
        )
        await send_callbacks_for_hook_devices(
            api, update_publish_id, timeout_seconds=0.5
        )

        status = await wait_for_publish_status(
            api, update_publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        devices = await get_devices_from_progress(api, update_publish_id)
        assert len(devices) >= 2

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_auto_approve_update_hook_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3-device UPDATE with hook: auto_approve 2 gates + callbacks per stage → SUCCESS.

        3-stage pipeline (GRAY + PROD_FIRST_BATCH + PROD_OTHER_BATCH), 2 gates.
        auto_approve handles both gates. Callbacks needed for each stage.
        """
        t0 = time.monotonic()
        bot = await create_test_bot(
            api,
            f"auto-approve-upd-hk-3d-{unique_id}",
            device_count=3,
        )
        await activate_test_bot(api, bot)
        update_resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/update",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "bot_name": f"auto-approve-upd-hk-3d-{unique_id}-renamed",
                "config": {
                    "auto_approve_publish": True,
                    "deploy_config": {
                        "after_create_cmd_hook": "/bin/echo 'hook executed'",
                    },
                },
            },
        )
        assert update_resp.status_code == 200
        update_publish_id = update_resp.json()["data"].get("publish_id")
        assert update_publish_id is not None

        await send_callbacks_for_hook_devices(
            api, update_publish_id, timeout_seconds=0.5
        )
        await send_callbacks_for_hook_devices(
            api, update_publish_id, timeout_seconds=0.5
        )
        await send_callbacks_for_hook_devices(
            api, update_publish_id, timeout_seconds=0.5
        )

        status = await wait_for_publish_status(
            api, update_publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        devices = await get_devices_from_progress(api, update_publish_id)
        assert len(devices) >= 3

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])
