"""E2E tests for auto_approve_publish flag on restart bot.

Verifies that when auto_approve_publish=True, the restart_bot call
sets auto_approve=True in the publish config. The background auto-approve
loop has limited iterations and may time out before callbacks complete,
so we use approve_and_complete to drive the pipeline to completion and
verify the config was set correctly.

RESTART pipeline: PROD_FIRST_BATCH (1 gate) -> PROD_OTHER_BATCH (auto-continue).

Auto-distribute for RESTART (replica_desired defaults to 1):
- All devices go to PROD_FIRST_BATCH (single stage, 1 gate)

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import logging
import time
import uuid

import pytest

from ..conftest import APITestHelper, cleanup_bot
from ..hook_helpers import (
    approve_and_complete,
    approve_publish,
    create_and_activate_bot,
    dump_publish_diagnostics,
    get_devices_from_progress,
    send_callbacks_for_hook_devices,
    wait_for_publish_status,
)

tlog = logging.getLogger("e2e.auto_approve_restart")

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


async def _restart_bot(
    api: APITestHelper,
    bot_uuid: str,
    scope: str = "all",
    auto_approve_publish: bool = False,
) -> int | None:
    """Trigger restart, return publish_id or None."""
    body = {
        "operator": "e2e-test",
        "request_id": uuid.uuid4().hex,
        "scope": scope,
    }
    if auto_approve_publish:
        body["auto_approve_publish"] = True

    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/restart",
        params=api.params(),
        json=body,
    )
    assert resp.status_code == 200, f"Restart failed: {resp.status_code} {resp.text}"
    return resp.json()["data"].get("publish_id")


async def _assert_publish_config_has_auto_approve(
    api: APITestHelper, publish_id: int
) -> None:
    """Verify publish config has auto_approve=True in extra_config."""
    resp = await api.client.get(
        api.publish_url(publish_id),
        params=api.params(),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    extra = data.get("extra_config") or {}
    assert extra.get("auto_approve") is True, (
        f"Expected auto_approve=True in publish config, got extra_config={extra}"
    )


# ── Auto-approve config verification ─────────────────────────────────────


class TestRestartAutoApproveConfig:
    """Verify auto_approve_publish is correctly passed to publish config."""

    @pytest.mark.asyncio
    async def test_restart_1_device_auto_approve_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: restart with auto_approve=True -> config.auto_approve=True, pipeline completes."""
        t0 = time.monotonic()
        bot = await create_and_activate_bot(
            api, f"rs-aa-cfg-1d-{unique_id}", device_count=1
        )
        tlog.info(f"[TIMING] create_and_activate: {time.monotonic() - t0:.2f}s")

        publish_id = await _restart_bot(api, bot["bot_uuid"], auto_approve_publish=True)
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        # Verify publish config recorded auto_approve=True
        await _assert_publish_config_has_auto_approve(api, publish_id)

        # Drive to completion (background loop may time out before callbacks)
        status = await approve_and_complete(api, publish_id, bot_uuid=bot["bot_uuid"])
        if status != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Bot should be ACTIVE
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        if resp.status_code == 200:
            assert resp.json()["data"]["status"] == "ACTIVE"

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_2_devices_auto_approve_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices: auto_approve config set correctly, pipeline completes."""
        t0 = time.monotonic()
        bot = await create_and_activate_bot(
            api, f"rs-aa-cfg-2d-{unique_id}", device_count=2
        )
        tlog.info(f"[TIMING] create_and_activate: {time.monotonic() - t0:.2f}s")

        publish_id = await _restart_bot(api, bot["bot_uuid"], auto_approve_publish=True)
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        await _assert_publish_config_has_auto_approve(api, publish_id)

        status = await approve_and_complete(api, publish_id, bot_uuid=bot["bot_uuid"])
        if status != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 2

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_3_devices_auto_approve_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices: auto_approve config set correctly, pipeline completes."""
        t0 = time.monotonic()
        bot = await create_and_activate_bot(
            api, f"rs-aa-cfg-3d-{unique_id}", device_count=3
        )
        tlog.info(f"[TIMING] create_and_activate: {time.monotonic() - t0:.2f}s")

        publish_id = await _restart_bot(api, bot["bot_uuid"], auto_approve_publish=True)
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        await _assert_publish_config_has_auto_approve(api, publish_id)

        status = await approve_and_complete(api, publish_id, bot_uuid=bot["bot_uuid"])
        if status != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 3

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])


# ── Backward compatibility ────────────────────────────────────────────────


class TestRestartBackwardCompat:
    """Restart without auto_approve_publish — manual /approve still required."""

    @pytest.mark.asyncio
    async def test_restart_requires_manual_approve(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device without auto_approve: must manually /approve then callback -> SUCCESS."""
        t0 = time.monotonic()
        bot = await create_and_activate_bot(api, f"rs-bc-{unique_id}", device_count=1)
        tlog.info(f"[TIMING] create_and_activate: {time.monotonic() - t0:.2f}s")

        publish_id = await _restart_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        # Without auto_approve: publish should be PENDING, waiting for manual approve
        status = await wait_for_publish_status(
            api,
            publish_id,
            {"PENDING", "ACTIVE", "APPROVING", "SUCCESS"},
            timeout_seconds=0.5,
        )
        if status == "SUCCESS":
            tlog.info("Publish reached SUCCESS without manual approve (fast mock)")
        else:
            assert status in ("PENDING", "APPROVING"), (
                f"Expected PENDING/APPROVING without auto_approve, got {status}"
            )

            code = await approve_publish(api, publish_id)
            assert code == 200

            await send_callbacks_for_hook_devices(api, publish_id)

            status = await wait_for_publish_status(
                api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
            )
            assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Bot should be ACTIVE
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        if resp.status_code == 200:
            assert resp.json()["data"]["status"] == "ACTIVE"

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_2_devices_manual_approve(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices without auto_approve: manual /approve + callbacks -> SUCCESS."""
        t0 = time.monotonic()
        bot = await create_and_activate_bot(
            api, f"rs-bc-2d-{unique_id}", device_count=2
        )
        tlog.info(f"[TIMING] create_and_activate: {time.monotonic() - t0:.2f}s")

        publish_id = await _restart_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await approve_and_complete(api, publish_id, bot_uuid=bot["bot_uuid"])
        if status != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])


# ── Scope with auto-approve ───────────────────────────────────────────────


class TestRestartScopeAutoApprove:
    """RESTART with scope parameter and auto_approve_publish=True."""

    @pytest.mark.asyncio
    async def test_restart_scope_all_auto_approve(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """scope='all' with auto_approve: config set, pipeline completes via approve_and_complete."""
        t0 = time.monotonic()
        bot = await create_and_activate_bot(
            api, f"rs-scope-all-aa-{unique_id}", device_count=1
        )
        tlog.info(f"[TIMING] create_and_activate: {time.monotonic() - t0:.2f}s")

        publish_id = await _restart_bot(
            api, bot["bot_uuid"], scope="all", auto_approve_publish=True
        )
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        await _assert_publish_config_has_auto_approve(api, publish_id)

        status = await approve_and_complete(api, publish_id, bot_uuid=bot["bot_uuid"])
        if status != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        tlog.info(f"[TIMING] test total: {time.monotonic() - t0:.2f}s")
        await cleanup_bot(api, bot["bot_uuid"])
