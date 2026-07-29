import json
import logging
import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    approve_publish,
    cleanup_bot,
    create_and_activate_bot,
    dump_publish_diagnostics,
    send_callbacks_for_hook_devices,
    send_mixed_callbacks,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e_asgi]

log = logging.getLogger("e2e.re_upgrade")


async def _update_bot_config(
    api: APITestHelper,
    bot_uuid: str,
    deploy_config: dict | None = None,
) -> int | None:
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
    assert resp.status_code == 200, f"UPDATE failed: {resp.status_code} {resp.text}"
    return resp.json()["data"].get("publish_id")


async def _publish_record_count(api: APITestHelper, publish_id: int) -> int:
    resp = await api.client.get(
        api.publish_url(publish_id, "progress"),
        params=api.params(include_devices="true"),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    total = 0
    for detail in data.get("device_details", []):
        devices = detail.get("devices", [])
        total += len(devices)
        for d in devices:
            log.debug(
                f"[RE_UPGRADE] publish_id={publish_id} "
                f"batch_id={detail.get('batch_id')} "
                f"stage={detail.get('stage')} "
                f"batch_status={detail.get('status')} "
                f"device_uuid={d.get('device_uuid', '?')[:12]} "
                f"device_id={d.get('device_id')} "
                f"event_type={d.get('event_type')} "
                f"result_status={d.get('result_status')} "
                f"result_message={str(d.get('result_message', ''))[:120]}"
            )
    log.info(
        f"[RE_UPGRADE] publish_id={publish_id} "
        f"total_records={total} "
        f"publish_status={data.get('status')} "
        f"overall_progress={json.dumps(data.get('overall_progress', {}))}"
    )
    return total


async def _get_bot_records_by_uuid(api: APITestHelper, bot_uuid: str) -> list[dict]:
    resp = await api.client.get(
        f"{api.bot_url(bot_uuid)}/detail-by-uuid",
        params=api.params(),
    )
    assert resp.status_code == 200, f"detail-by-uuid failed: {resp.status_code}"
    items = resp.json()["data"].get("items", [])
    for item in items:
        log.info(
            f"[BOT_RECORDS] bot_id={item['id']} bot_uuid={item['bot_uuid']} "
            f"status={item['status']} devices={len(item.get('devices', []))}"
        )
    return items


class TestReUpgradeAfterFailure:
    @pytest.mark.asyncio
    async def test_upgrade_after_device_failed_creates_publish_records(
        self,
        api: APITestHelper,
        unique_id: str,
    ) -> None:
        bot = await create_and_activate_bot(
            api,
            f"re-upgrade-{unique_id}",
            device_count=1,
        )

        # Make the 1 device FAILED via restart with failed callback
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
        restart_pid = restart_resp.json()["data"].get("publish_id")
        assert restart_pid is not None

        code = await approve_publish(api, restart_pid)
        assert code == 200

        await send_mixed_callbacks(api, restart_pid, fail_index=0)

        restart_status = await wait_for_publish_status(
            api,
            restart_pid,
            {"FAILED"},
            timeout_seconds=0.5,
        )
        assert restart_status == "FAILED", f"Expected FAILED, got {restart_status}"

        # Now upgrade the bot — must have publish_record entries
        new_deploy = {"after_create_cmd_hook": "/bin/echo 'upgrade after fail'"}
        upgrade_pid = await _update_bot_config(api, bot["bot_uuid"], new_deploy)
        assert upgrade_pid is not None

        code = await approve_publish(api, upgrade_pid)
        assert code == 200, f"Approve upgrade failed: {code}"

        count = await _publish_record_count(api, upgrade_pid)
        log.info(
            f"[RE_UPGRADE] ASSERT: upgrade_pid={upgrade_pid} "
            f"bot_uuid={bot['bot_uuid']} record_count={count}"
        )
        if count == 0:
            await dump_publish_diagnostics(
                api, upgrade_pid, bot_uuid=bot["bot_uuid"], label="RE_UPGRADE_FAIL"
            )
        assert count >= 1, (
            f"Expected at least 1 publish_record for upgrade publish {upgrade_pid}, "
            f"got {count}. devices_to_update was empty in _execute_update_batch."
        )

        await send_callbacks_for_hook_devices(api, upgrade_pid)
        final_status = await wait_for_publish_status(
            api,
            upgrade_pid,
            {"SUCCESS", "FAILED"},
            timeout_seconds=30,
        )
        assert final_status == "SUCCESS", f"Expected SUCCESS, got {final_status}"

        await cleanup_bot(api, bot["bot_uuid"])


class TestReUpgradeBotRecordBug:
    """Reproduce the bug where a PENDING clone from a failed upgrade
    is selected instead of the operational ACTIVE/FAILED record.

    Scenario:
    1. Create and activate a bot with 1 device
    2. Trigger upgrade (creates PENDING clone) — this upgrade FAILS
        → Old record stays ACTIVE, PENDING clone remains orphaned
    3. Trigger another upgrade — BUG: the orphaned PENDING clone
        (higher ID) is picked instead of the ACTIVE record, resulting
        in a clone with no devices
    4. Verify the ACTIVE record has devices (not cloned from a
        PENDING/RELEASED record with no devices)
    """

    @pytest.mark.asyncio
    async def test_re_upgrade_uses_correct_bot_record(
        self,
        api: APITestHelper,
        unique_id: str,
    ) -> None:
        bot = await create_and_activate_bot(
            api,
            f"re-upgrade-bug-{unique_id}",
            device_count=1,
        )
        bot_uuid = bot["bot_uuid"]

        # 1st upgrade: trigger config update → FAIL
        deploy_1 = {"after_create_cmd_hook": "/bin/echo 'first-upgrade'"}
        pid_1 = await _update_bot_config(api, bot_uuid, deploy_1)
        assert pid_1 is not None
        code = await approve_publish(api, pid_1)
        assert code == 200

        await send_mixed_callbacks(api, pid_1, fail_index=0)
        status_1 = await wait_for_publish_status(
            api, pid_1, {"FAILED"}, timeout_seconds=30
        )
        assert status_1 == "FAILED"

        # Verify PENDING clone was cleaned up after failed upgrade
        records = await _get_bot_records_by_uuid(api, bot_uuid)
        pending_records = [r for r in records if r["status"] == "PENDING"]
        log.info(
            f"After 1st upgrade FAIL: bot records={[r['status'] for r in records]}"
        )
        assert len(pending_records) == 0, (
            f"Expected PENDING clone to be cleaned up after UPDATE publish FAILURE, "
            f"but found {len(pending_records)}: "
            f"{[(r['id'], r['status']) for r in pending_records]}"
        )

        # 2nd upgrade: should use the ACTIVE record (which has devices),
        # not the PENDING clone (which has none)
        deploy_2 = {"after_create_cmd_hook": "/bin/echo 'second-upgrade'"}
        pid_2 = await _update_bot_config(api, bot_uuid, deploy_2)
        assert pid_2 is not None
        code = await approve_publish(api, pid_2)
        assert code == 200

        await send_callbacks_for_hook_devices(api, pid_2)
        count = await _publish_record_count(api, pid_2)
        assert count >= 1, (
            "No publish_records — upgrade cloned from a record with no devices"
        )

        status_2 = await wait_for_publish_status(
            api, pid_2, {"SUCCESS", "FAILED"}, timeout_seconds=30
        )
        assert status_2 == "SUCCESS"

        # Final: ACTIVE record must have devices
        records = await _get_bot_records_by_uuid(api, bot_uuid)
        active = [r for r in records if r["status"] == "ACTIVE"]
        assert len(active) == 1, f"Expected 1 ACTIVE record, got {len(active)}"
        assert len(active[0].get("devices", [])) >= 1, (
            "ACTIVE record has no devices — bug: cloned from wrong source"
        )

        await cleanup_bot(api, bot_uuid)
