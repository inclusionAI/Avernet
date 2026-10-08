"""E2E tests for async device hook callback flow: RESTART publish type.

RESTART uses before_destroy_cmd_hook (sync) + after_create_cmd_hook (async, callback-driven).
The after_create start hook requires external callback to complete.
2-stage pipeline: PROD_FIRST_BATCH → PROD_OTHER_BATCH (1 gate).

Auto-distribute for RESTART:
- 1 device → [PROD_FIRST_BATCH] (1 stage, 1 gate)
- 2+ devices → [PROD_FIRST_BATCH, PROD_OTHER_BATCH] (PROD_OTHER auto-continues)

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    approve_and_complete,
    approve_publish,
    cleanup_bot,
    create_and_activate_bot,
    dump_publish_diagnostics,
    send_callbacks_for_hook_devices,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e_asgi]


async def _restart_bot(
    api: APITestHelper, bot_uuid: str, scope: str = "all"
) -> int | None:
    """Trigger restart, return publish_id or None."""
    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/restart",
        params=api.params(),
        json={
            "operator": "e2e-test",
            "request_id": uuid.uuid4().hex,
            "scope": scope,
        },
    )
    assert resp.status_code == 200
    return resp.json()["data"].get("publish_id")


# ── Success path ─────────────────────────────────────────────────────────────


class TestRestartSuccess:
    """RESTART with hook: approve → callback SUCCESS → publish SUCCESS."""

    @pytest.mark.asyncio
    async def test_restart_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: restart → approve → callback → SUCCESS, bot ACTIVE."""
        bot = await create_and_activate_bot(
            api, f"restart-hook-1d-{unique_id}", device_count=1
        )
        publish_id = await _restart_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        # 1. Approve
        code = await approve_publish(api, publish_id)
        assert code == 200

        # 2. Send callback for start hook
        await send_callbacks_for_hook_devices(api, publish_id)

        # 3. Verify publish succeeded
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # 4. Bot should be ACTIVE
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        if resp.status_code == 200:
            assert resp.json()["data"]["status"] == "ACTIVE"

        # 5. Verify devices are ACTIVE via detail-by-uuid
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        active_devices = []
        for item in items:
            active_devices.extend(
                [d for d in item["devices"] if d["status"] == "ACTIVE"]
            )
        assert len(active_devices) >= 1

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices: Auto-distribute to [PROD_FIRST_BATCH, PROD_OTHER_BATCH].

        PROD_FIRST_BATCH has pause_for_approval=True, PROD_OTHER auto-continues.
        """
        bot = await create_and_activate_bot(
            api, f"restart-hook-2d-{unique_id}", device_count=2
        )
        publish_id = await _restart_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        status = await approve_and_complete(api, publish_id, bot_uuid=bot["bot_uuid"])
        if status != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices: Auto-distribute to [PROD_FIRST_BATCH, PROD_OTHER_BATCH].

        PROD_FIRST_BATCH has pause_for_approval=True, PROD_OTHER auto-continues.
        """
        bot = await create_and_activate_bot(
            api, f"restart-hook-3d-{unique_id}", device_count=3
        )
        publish_id = await _restart_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        status = await approve_and_complete(api, publish_id, bot_uuid=bot["bot_uuid"])
        if status != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])


# ── Failure path ─────────────────────────────────────────────────────────────


class TestRestartFailure:
    """RESTART with hook: FAILED callback → publish FAILED, device set to FAILED."""

    @pytest.mark.asyncio
    async def test_restart_1_device_hook_failure(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: restart → approve → FAILED callback → publish FAILED, device FAILED."""
        bot = await create_and_activate_bot(
            api, f"restart-fail-1d-{unique_id}", device_count=1
        )
        publish_id = await _restart_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=1,
            stderr="restart hook failed",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        all_devices = []
        for item in items:
            all_devices.extend(item.get("devices", []))
        failed_devices = [d for d in all_devices if d["status"] == "FAILED"]
        assert len(failed_devices) >= 1, (
            f"Expected device FAILED after hook failure, "
            f"got statuses: {[d['status'] for d in all_devices]}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_2_devices_one_fails(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices: approve → FAILED callback on stage 1 → FAILED.

        Auto-distribute to [PROD_FIRST_BATCH, PROD_OTHER_BATCH], 1 device per stage.
        """
        bot = await create_and_activate_bot(
            api, f"restart-mixed-{unique_id}", device_count=2
        )
        publish_id = await _restart_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        code = await approve_publish(api, publish_id)
        assert code == 200

        # Fail the only device in stage 1
        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=1,
            stderr="restart first device failed",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_3_devices_one_fails(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices: approve → FAILED callback on first stage → FAILED."""
        bot = await create_and_activate_bot(
            api, f"restart-3d-fail-{unique_id}", device_count=3
        )
        publish_id = await _restart_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=1,
            stderr="restart stage failure",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_callback_fail_then_retry_recovers(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Callback FAILED → device FAILED → re-restart with SUCCESS callback → device ACTIVE.

        1. Restart with FAILED callback → device becomes FAILED
        2. Restart again with SUCCESS callback → FAILED device is selected and recovered to ACTIVE
        """
        bot = await create_and_activate_bot(
            api, f"restart-fail-retry-{unique_id}", device_count=1
        )

        publish_id_1 = await _restart_bot(api, bot["bot_uuid"])
        if not publish_id_1:
            pytest.skip("No publish_id returned from first restart")

        code = await approve_publish(api, publish_id_1)
        assert code == 200

        await send_callbacks_for_hook_devices(
            api,
            publish_id_1,
            result_status="FAILED",
            exit_code=1,
            stderr="first restart failed",
        )

        status_1 = await wait_for_publish_status(
            api, publish_id_1, {"FAILED"}, timeout_seconds=0.5
        )
        assert status_1 == "FAILED", f"First restart should FAIL, got {status_1}"

        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        all_devices = []
        for item in items:
            all_devices.extend(item.get("devices", []))
        assert any(d["status"] == "FAILED" for d in all_devices), (
            f"Expected FAILED device after hook failure, "
            f"got: {[d['status'] for d in all_devices]}"
        )

        publish_id_2 = await _restart_bot(api, bot["bot_uuid"], scope="all")
        if not publish_id_2:
            pytest.skip("No publish_id returned from retry restart")

        status_2 = await approve_and_complete(
            api, publish_id_2, bot_uuid=bot["bot_uuid"]
        )
        if status_2 != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id_2, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status_2 == "SUCCESS", (
            f"Retry restart should succeed (FAILED device should be selected), "
            f"got {status_2}"
        )

        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        active_devices = []
        for item in items:
            active_devices.extend(
                [d for d in item.get("devices", []) if d["status"] == "ACTIVE"]
            )
        assert len(active_devices) >= 1, (
            f"Expected ACTIVE device after recovery restart, "
            f"got statuses: {[d['status'] for item in items for d in item.get('devices', [])]}"
        )

        await cleanup_bot(api, bot["bot_uuid"])


# ── Scope tests ─────────────────────────────────────────────────────────────


class TestRestartScope:
    """RESTART with scope parameter: 'all' (default) and 'unhealthy'."""

    @pytest.mark.asyncio
    async def test_restart_scope_all_explicit(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """scope='all' explicitly: same as default, restarts ACTIVE devices."""
        bot = await create_and_activate_bot(
            api, f"restart-scope-all-{unique_id}", device_count=1
        )
        publish_id = await _restart_bot(api, bot["bot_uuid"], scope="all")
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        status = await approve_and_complete(api, publish_id, bot_uuid=bot["bot_uuid"])
        if status != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_scope_unhealthy_no_failed_devices(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """scope='unhealthy' with no FAILED devices: expect 400 error."""
        bot = await create_and_activate_bot(
            api, f"restart-scope-unhealthy-nofail-{unique_id}", device_count=1
        )
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/restart",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "scope": "unhealthy",
            },
        )
        # No FAILED devices → should return error (400 or 500)
        assert resp.status_code in (400, 500), (
            f"Expected error for unhealthy scope with no failed devices, got {resp.status_code}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_scope_default_is_all(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """No scope specified: defaults to 'all', restarts ACTIVE devices."""
        bot = await create_and_activate_bot(
            api, f"restart-scope-default-{unique_id}", device_count=1
        )
        # Call _restart_bot without scope (uses default "all")
        publish_id = await _restart_bot(api, bot["bot_uuid"])
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        status = await approve_and_complete(api, publish_id, bot_uuid=bot["bot_uuid"])
        if status != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_scope_unhealthy_restarts_failed_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """scope='unhealthy': restart only the FAILED device, skip ACTIVE ones.

        1. Create 2-device bot, activate
        2. Restart with scope='all', fail one device via callback
        3. Verify one device is FAILED
        4. Restart with scope='unhealthy' — only the FAILED device should be picked up
        5. Approve and callback SUCCESS → FAILED device becomes ACTIVE
        """
        from tests.e2e.asgi.conftest import send_mixed_callbacks

        # Step 1: Create and activate 2-device bot
        bot = await create_and_activate_bot(
            api, f"restart-unhealthy-failed-{unique_id}", device_count=2
        )

        # Step 2: Restart with scope='all', then fail one device via mixed callback
        publish_id_1 = await _restart_bot(api, bot["bot_uuid"], scope="all")
        if not publish_id_1:
            pytest.skip("No publish_id returned from first restart")

        code = await approve_publish(api, publish_id_1)
        assert code == 200

        # Fail the first device, succeed the second
        await send_mixed_callbacks(api, publish_id_1, fail_index=0)

        status_1 = await wait_for_publish_status(
            api, publish_id_1, {"FAILED"}, timeout_seconds=0.5
        )
        assert status_1 == "FAILED", f"Expected FAILED, got {status_1}"

        # Step 3: Verify one device is FAILED
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        all_devices = []
        for item in items:
            all_devices.extend(item.get("devices", []))
        failed_devices = [d for d in all_devices if d["status"] == "FAILED"]
        assert len(failed_devices) >= 1, (
            f"Expected at least 1 FAILED device after failed callback, "
            f"got statuses: {[d['status'] for d in all_devices]}"
        )

        # Step 4: Restart with scope='unhealthy' — should only pick up FAILED devices
        publish_id_2 = await _restart_bot(api, bot["bot_uuid"], scope="unhealthy")
        if not publish_id_2:
            pytest.skip("No publish_id returned from unhealthy restart")

        code = await approve_publish(api, publish_id_2)
        assert code == 200

        # Step 5: Send SUCCESS callback for the restarted device
        await send_callbacks_for_hook_devices(api, publish_id_2)

        status_2 = await wait_for_publish_status(
            api, publish_id_2, {"SUCCESS"}, timeout_seconds=0.5
        )
        if status_2 != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id_2, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status_2 == "SUCCESS", f"Expected SUCCESS, got {status_2}"

        # Verify all devices are now ACTIVE
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        active_devices = []
        for item in items:
            active_devices.extend(
                [d for d in item.get("devices", []) if d["status"] == "ACTIVE"]
            )
        assert len(active_devices) >= 2, (
            f"Expected all devices ACTIVE after unhealthy restart, "
            f"got: {[d['status'] for item in items for d in item.get('devices', [])]}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_scope_all_includes_failed_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """scope='all': restart both ACTIVE and FAILED devices.

        1. Create 2-device bot, activate
        2. Restart with scope='all', fail one device via callback
        3. Verify one device is FAILED
        4. Restart with scope='all' — both ACTIVE and FAILED devices should be picked up
        5. Approve and callback SUCCESS → all devices become ACTIVE
        """
        from tests.e2e.asgi.conftest import send_mixed_callbacks

        # Step 1: Create and activate 2-device bot
        bot = await create_and_activate_bot(
            api, f"restart-all-incl-failed-{unique_id}", device_count=2
        )

        # Step 2: Restart with scope='all', then fail one device via mixed callback
        publish_id_1 = await _restart_bot(api, bot["bot_uuid"], scope="all")
        if not publish_id_1:
            pytest.skip("No publish_id returned from first restart")

        code = await approve_publish(api, publish_id_1)
        assert code == 200

        # Fail the first device, succeed the second
        await send_mixed_callbacks(api, publish_id_1, fail_index=0)

        status_1 = await wait_for_publish_status(
            api, publish_id_1, {"FAILED"}, timeout_seconds=0.5
        )
        assert status_1 == "FAILED", f"Expected FAILED, got {status_1}"

        # Step 3: Verify one device is FAILED
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        all_devices = []
        for item in items:
            all_devices.extend(item.get("devices", []))
        failed_devices = [d for d in all_devices if d["status"] == "FAILED"]
        assert len(failed_devices) >= 1, (
            f"Expected at least 1 FAILED device after failed callback, "
            f"got statuses: {[d['status'] for d in all_devices]}"
        )

        # Step 4: Restart with scope='all' — should pick up both ACTIVE and FAILED
        publish_id_2 = await _restart_bot(api, bot["bot_uuid"], scope="all")
        if not publish_id_2:
            pytest.skip("No publish_id returned from all-scope restart")

        # Step 5: Approve and send callbacks for all devices
        status_2 = await approve_and_complete(
            api, publish_id_2, bot_uuid=bot["bot_uuid"]
        )
        if status_2 != "SUCCESS":
            await dump_publish_diagnostics(
                api, publish_id_2, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status_2 == "SUCCESS", f"Expected SUCCESS, got {status_2}"

        # Verify all devices are now ACTIVE
        resp = await api.client.get(
            f"{api.bot_url(bot['bot_uuid'])}/detail-by-uuid",
            params=api.params(),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        active_devices = []
        for item in items:
            active_devices.extend(
                [d for d in item.get("devices", []) if d["status"] == "ACTIVE"]
            )
        assert len(active_devices) >= 2, (
            f"Expected all devices ACTIVE after all-scope restart, "
            f"got: {[d['status'] for item in items for d in item.get('devices', [])]}"
        )

        await cleanup_bot(api, bot["bot_uuid"])
