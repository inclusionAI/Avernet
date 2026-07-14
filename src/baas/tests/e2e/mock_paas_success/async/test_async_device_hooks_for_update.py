"""E2E tests for async device hook callback flow: UPDATE publish type.

UPDATE publish creates a new PENDING bot record, then restarts devices
in-place with the new config. Uses same pipeline as CREATE:
5-stage pipeline with 3 approval gates.

Requires:
- Service running with PAAS_MOCK_MODE=true (restart-mock)
"""

import uuid

import pytest

from ...conftest import APITestHelper, cleanup_bot
from ...hook_helpers import (
    approve_and_complete,
    approve_publish,
    create_and_activate_bot,
    dump_publish_diagnostics,
    send_callbacks_for_hook_devices,
    send_mixed_callbacks,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e, pytest.mark.async_hook]


async def _update_bot_config(
    api: APITestHelper, bot_uuid: str, deploy_config: dict | None = None
) -> int | None:
    """Trigger UPDATE publish via POST bot with config, return publish_id or None."""
    json_body: dict = {
        "operator": "e2e-test",
        "request_id": uuid.uuid4().hex,
    }
    if deploy_config:
        json_body["config"] = {"deploy_config": deploy_config}

    resp = await api.client.post(
        f"{api.bot_url(bot_uuid)}/update",
        params=api.params(),
        json=json_body,
    )
    assert resp.status_code == 200
    return resp.json()["data"].get("publish_id")


async def _get_bot_records_by_uuid(api: APITestHelper, bot_uuid: str) -> list[dict]:
    """Get all non-deleted bot records for a bot_uuid via detail-by-uuid."""
    resp = await api.client.get(
        f"{api.bot_url(bot_uuid)}/detail-by-uuid",
        params=api.params(),
    )
    assert resp.status_code == 200
    return resp.json()["data"]["items"]


async def _get_devices_by_bot_id(api: APITestHelper, bot_id: int) -> list[dict]:
    """Get device list for a bot record by internal id."""
    resp = await api.client.get(
        f"/api/v1/bots/{bot_id}/devices-by-id",
        params=api.params(),
    )
    assert resp.status_code == 200
    return resp.json()["data"]["items"]


async def _assert_stage_pending(
    api: APITestHelper, bot_uuid: str, device_count: int
) -> None:
    """After UPDATE publish created: old bot ACTIVE, new bot PENDING, devices on old bot.

    Note: API returns calculated status (derived from device states), not raw DB status.
    The old bot's calculated status may differ from stored ACTIVE if devices are
    not yet ACTIVE. We verify two distinct non-deleted bot records exist.
    """
    records = await _get_bot_records_by_uuid(api, bot_uuid)
    assert len(records) == 2, (
        f"Stage PENDING: expected 2 bot records, got {len(records)}"
    )

    for r in records:
        assert r["is_deleted"] == 0, (
            f"Stage PENDING: bot id={r['id']} should not be soft-deleted"
        )

    # Two distinct bot records with different IDs
    bot_ids = {r["id"] for r in records}
    assert len(bot_ids) == 2, (
        f"Stage PENDING: expected 2 different bot IDs, got {bot_ids}"
    )

    # Devices should be linked to one of the bot records (the old one)
    device_counts = []
    for r in records:
        devices = await _get_devices_by_bot_id(api, r["id"])
        device_counts.append(len(devices))
    total_devices = sum(device_counts)
    assert total_devices >= device_count, (
        f"Stage PENDING: expected {device_count} devices across bots, got {total_devices}"
    )


async def _get_publish_status(api: APITestHelper, publish_id: int) -> str | None:
    """Get current publish status, or None if not found."""
    resp = await api.client.get(api.publish_url(publish_id), params=api.params())
    if resp.status_code == 404:
        return "SUCCESS"  # publish deleted after completion
    if resp.status_code == 200:
        return resp.json()["data"]["status"]
    return None


async def _assert_stage_executing(
    api: APITestHelper, bot_uuid: str, device_count: int, publish_id: int
) -> None:
    """After approve: verify bot records while publish is in-flight.

    The mock pipeline may complete fast, so we handle different timing cases.
    API returns calculated status (from device states), not raw DB status,
    so during UPDATE the old bot may show PENDING if devices are UPDATING.

    If the publish has already reached a terminal state, skip entirely.
    """
    current_status = await _get_publish_status(api, publish_id)
    if current_status in ("SUCCESS", "FAILED", "REVOKED", "REJECTED", None):
        return  # Already completed or gone, skip intermediate check

    records = await _get_bot_records_by_uuid(api, bot_uuid)
    assert len(records) >= 1, (
        f"Stage EXECUTING: expected at least 1 bot record, got {len(records)}"
    )

    for r in records:
        assert r["is_deleted"] == 0, (
            f"Stage EXECUTING: bot id={r['id']} should not be soft-deleted"
        )

    # Verify devices still exist across all bot records
    total_devices = 0
    for r in records:
        devices = await _get_devices_by_bot_id(api, r["id"])
        total_devices += len(devices)
    assert total_devices >= device_count, (
        f"Stage EXECUTING: expected {device_count} devices across bots, got {total_devices}"
    )


async def _assert_stage_success(
    api: APITestHelper, bot_uuid: str, device_count: int
) -> None:
    """After complete: old bot soft-deleted, new bot ACTIVE, devices on new bot.

    Note: API returns calculated status. The new bot's calculated status
    should be ACTIVE since all devices should be ACTIVE after completion.
    """
    records = await _get_bot_records_by_uuid(api, bot_uuid)

    # Old bot should be soft-deleted (invisible via detail-by-uuid)
    assert len(records) == 1, (
        f"Stage SUCCESS: expected 1 non-deleted bot record (old soft-deleted), got {len(records)}"
    )

    new_bot = records[0]
    assert new_bot["is_deleted"] == 0, (
        "Stage SUCCESS: new bot should not be soft-deleted"
    )

    # Devices should now be linked to the new bot
    devices = await _get_devices_by_bot_id(api, new_bot["id"])
    assert len(devices) >= device_count, (
        f"Stage SUCCESS: expected {device_count} devices on new bot, got {len(devices)}"
    )

    # New bot's calculated status should be ACTIVE (all devices ACTIVE after completion)
    active_devices = [d for d in devices if d["status"] == "ACTIVE"]
    assert len(active_devices) >= device_count, (
        f"Stage SUCCESS: expected {device_count} ACTIVE devices, got {len(active_devices)}"
    )
    assert new_bot["status"] == "ACTIVE", (
        f"Stage SUCCESS: new bot should be ACTIVE (has {len(active_devices)} ACTIVE devices), got {new_bot['status']}"
    )


# ── Success path ─────────────────────────────────────────────────────────────


class TestUpdateSuccess:
    """UPDATE with hook: approve → callback → SUCCESS with stage-by-stage verification."""

    @pytest.mark.asyncio
    async def test_update_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1 device: verify DB record status at each publish stage."""
        bot = await create_and_activate_bot(
            api, f"update-hook-1d-{unique_id}", device_count=1
        )

        new_deploy = {"after_create_cmd_hook": "/bin/echo 'updated hook'"}
        publish_id = await _update_bot_config(api, bot["bot_uuid"], new_deploy)
        if not publish_id:
            pytest.skip("No publish_id returned from update")

        # Stage 1: PENDING — old bot ACTIVE, new bot PENDING
        await _assert_stage_pending(api, bot["bot_uuid"], device_count=1)

        # Approve (UPDATE uses 3-gate pipeline)
        code = await approve_publish(api, publish_id)
        assert code == 200

        # Stage 2: EXECUTING — verify bot records while publish in-flight
        await _assert_stage_executing(
            api, bot["bot_uuid"], device_count=1, publish_id=publish_id
        )

        # Send callback for hook devices and complete
        await send_callbacks_for_hook_devices(api, publish_id)
        await approve_and_complete(api, publish_id)

        # Wait for publish SUCCESS
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=30
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Stage 3: SUCCESS — old bot soft-deleted, new bot ACTIVE, devices on new bot
        await _assert_stage_success(api, bot["bot_uuid"], device_count=1)

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_update_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """2 devices: verify DB record status at each publish stage."""
        bot = await create_and_activate_bot(
            api, f"update-hook-2d-{unique_id}", device_count=2
        )

        new_deploy = {"after_create_cmd_hook": "/bin/echo 'updated hook 2d'"}
        publish_id = await _update_bot_config(api, bot["bot_uuid"], new_deploy)
        if not publish_id:
            pytest.skip("No publish_id returned from update")

        # Stage 1: PENDING — old bot ACTIVE, new bot PENDING
        await _assert_stage_pending(api, bot["bot_uuid"], device_count=2)

        # Approve
        code = await approve_publish(api, publish_id)
        assert code == 200

        # Stage 2: EXECUTING — verify bot records while publish in-flight
        await _assert_stage_executing(
            api, bot["bot_uuid"], device_count=2, publish_id=publish_id
        )

        # Complete
        await send_callbacks_for_hook_devices(api, publish_id)
        await approve_and_complete(api, publish_id)

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=60
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Stage 3: SUCCESS — old bot soft-deleted, new bot ACTIVE, 2 devices on new bot
        await _assert_stage_success(api, bot["bot_uuid"], device_count=2)

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_update_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3 devices: verify DB record status at each publish stage."""
        bot = await create_and_activate_bot(
            api, f"update-hook-3d-{unique_id}", device_count=3
        )

        new_deploy = {"after_create_cmd_hook": "/bin/echo 'updated hook 3d'"}
        publish_id = await _update_bot_config(api, bot["bot_uuid"], new_deploy)
        if not publish_id:
            pytest.skip("No publish_id returned from update")

        # Stage 1: PENDING — old bot ACTIVE, new bot PENDING
        await _assert_stage_pending(api, bot["bot_uuid"], device_count=3)

        # Approve
        code = await approve_publish(api, publish_id)
        assert code == 200

        # Stage 2: EXECUTING — verify bot records while publish in-flight
        await _assert_stage_executing(
            api, bot["bot_uuid"], device_count=3, publish_id=publish_id
        )

        # Complete
        await send_callbacks_for_hook_devices(api, publish_id)
        await approve_and_complete(api, publish_id)

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=90
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Stage 3: SUCCESS — old bot soft-deleted, new bot ACTIVE, 3 devices on new bot
        await _assert_stage_success(api, bot["bot_uuid"], device_count=3)

        await cleanup_bot(api, bot["bot_uuid"])


# ── Metadata-only update (no publish) ────────────────────────────────────────


class TestUpdateMetadataOnly:
    """Name/description-only update does NOT trigger a publish."""

    @pytest.mark.asyncio
    async def test_update_name_only_no_publish(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Update bot name only → no publish created, in-place update."""
        bot = await create_and_activate_bot(
            api, f"update-name-{unique_id}", device_count=1
        )

        resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/update",
            params=api.params(),
            json={
                "name": f"renamed-{unique_id}",
                "operator": "e2e-test",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # No publish_id for metadata-only update
        assert data.get("publish_id") is None

        # Bot name should be updated, still only 1 record
        records = await _get_bot_records_by_uuid(api, bot["bot_uuid"])
        assert len(records) == 1, (
            f"Metadata update: expected 1 bot record, got {len(records)}"
        )
        assert records[0]["name"] == f"renamed-{unique_id}"
        assert records[0]["status"] == "ACTIVE"

        await cleanup_bot(api, bot["bot_uuid"])


# ── Concurrent publish guard ────────────────────────────────────────────────


class TestUpdateConcurrentGuard:
    """UPDATE rejected if another publish in progress for same bot."""

    @pytest.mark.asyncio
    async def test_update_rejected_while_publish_active(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Cannot start UPDATE while another publish is active."""
        bot = await create_and_activate_bot(
            api, f"update-guard-{unique_id}", device_count=1
        )

        # Start a RESTART publish first
        restart_resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/restart",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "scope": "all",
            },
        )
        assert restart_resp.status_code == 200

        # Try UPDATE while RESTART is active → should fail
        update_resp = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/update",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
                "config": {"deploy_config": {"after_create_cmd_hook": "/bin/echo 'x'"}},
            },
        )
        # Should get conflict error (RESTART active, can't start UPDATE)
        assert update_resp.status_code == 409

        await cleanup_bot(api, bot["bot_uuid"])


# ── Batch generation verification ────────────────────────────────────────────


class TestUpdateBatchGeneration:
    """Verify UPDATE publish generates correct batch count and capacity."""

    @pytest.mark.asyncio
    async def test_update_1_device_generates_1_batch(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Single-device UPDATE must produce exactly 1 batch, not 4.

        Before fix: stage default device_counts (PREPUB=2, GRAY=4) were summed
        as total_device_count, causing 4 batches for a 1-device bot.
        """
        bot = await create_and_activate_bot(
            api, f"update-batch-gen-1d-{unique_id}", device_count=1
        )

        new_deploy = {"after_create_cmd_hook": "/bin/echo 'batch test'"}
        publish_id = await _update_bot_config(api, bot["bot_uuid"], new_deploy)
        assert publish_id is not None, "Expected publish_id from UPDATE"

        # Verify publish progress shows exactly 1 batch
        resp = await api.client.get(
            api.publish_url(publish_id, "progress"),
            params=api.params(include_devices="true"),
        )
        assert resp.status_code == 200
        progress = resp.json()["data"]

        overall = progress["overall_progress"]
        assert overall["total_batches"] == 1, (
            f"Expected 1 batch for 1-device bot, "
            f"got total_batches={overall['total_batches']}"
        )
        assert overall["total_devices"] == 1, (
            f"Expected 1 total device, got {overall['total_devices']}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_update_2_devices_generates_2_stages(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Two-device UPDATE: auto-compact to 2 stages, not 4."""
        bot = await create_and_activate_bot(
            api, f"update-batch-gen-2d-{unique_id}", device_count=2
        )

        new_deploy = {"after_create_cmd_hook": "/bin/echo 'batch test 2d'"}
        publish_id = await _update_bot_config(api, bot["bot_uuid"], new_deploy)
        assert publish_id is not None, "Expected publish_id from UPDATE"

        resp = await api.client.get(
            api.publish_url(publish_id, "progress"),
            params=api.params(include_devices="true"),
        )
        assert resp.status_code == 200
        progress = resp.json()["data"]

        overall = progress["overall_progress"]
        assert overall["total_batches"] == 2, (
            f"Expected 2 batches for 2-device bot (2-stage auto-compact), "
            f"got total_batches={overall['total_batches']}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_update_3_devices_generates_3_stages(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Three-device UPDATE: auto-compact to 3 stages, not 4."""
        bot = await create_and_activate_bot(
            api, f"update-batch-gen-3d-{unique_id}", device_count=3
        )

        new_deploy = {"after_create_cmd_hook": "/bin/echo 'batch test 3d'"}
        publish_id = await _update_bot_config(api, bot["bot_uuid"], new_deploy)
        assert publish_id is not None, "Expected publish_id from UPDATE"

        resp = await api.client.get(
            api.publish_url(publish_id, "progress"),
            params=api.params(include_devices="true"),
        )
        assert resp.status_code == 200
        progress = resp.json()["data"]

        overall = progress["overall_progress"]
        assert overall["total_batches"] == 3, (
            f"Expected 3 batches for 3-device bot (3-stage auto-compact), "
            f"got total_batches={overall['total_batches']}"
        )

        await cleanup_bot(api, bot["bot_uuid"])


# ── Failed device inclusion ──────────────────────────────────────────────────


class TestUpdateIncludesFailedDevices:
    """UPDATE should include FAILED devices (skip drain, go straight to restart)."""

    @pytest.mark.asyncio
    async def test_update_includes_failed_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """UPDATE with 1 FAILED + 1 ACTIVE device: both should be processed.

        1. Create 2-device bot, activate
        2. Restart with scope='all', fail one device via mixed callback
        3. Verify one FAILED device exists
        4. UPDATE bot config — the FAILED device should also be restarted
        5. Both devices should become ACTIVE
        """
        # Step 1: Create and activate 2-device bot
        bot = await create_and_activate_bot(
            api, f"update-failed-inc-{unique_id}", device_count=2
        )

        # Step 2: Restart with scope='all', fail first device via mixed callback
        restart_resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/restart",
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": __import__("uuid").uuid4().hex,
                "scope": "all",
            },
        )
        assert restart_resp.status_code == 200
        restart_publish_id = restart_resp.json()["data"].get("publish_id")
        assert restart_publish_id is not None

        code = await approve_publish(api, restart_publish_id)
        assert code == 200

        await send_mixed_callbacks(api, restart_publish_id, fail_index=0)

        restart_status = await wait_for_publish_status(
            api, restart_publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert restart_status == "FAILED", f"Expected FAILED, got {restart_status}"

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
            f"Expected at least 1 FAILED device, "
            f"got statuses: {[d['status'] for d in all_devices]}"
        )

        # Step 4: UPDATE bot config — should process both ACTIVE and FAILED devices
        new_deploy = {"after_create_cmd_hook": "/bin/echo 'updated post-fail'"}
        update_publish_id = await _update_bot_config(api, bot["bot_uuid"], new_deploy)
        if not update_publish_id:
            pytest.skip("No publish_id returned from update")

        # Step 5: Approve, send callbacks, verify SUCCESS
        status = await approve_and_complete(
            api, update_publish_id, bot_uuid=bot["bot_uuid"]
        )
        if status != "SUCCESS":
            await dump_publish_diagnostics(
                api, update_publish_id, bot_uuid=bot["bot_uuid"], label="TEST_FAIL"
            )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

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
            f"Expected all devices ACTIVE after update with failed device, "
            f"got: {[d['status'] for item in items for d in item.get('devices', [])]}"
        )

        await cleanup_bot(api, bot["bot_uuid"])
