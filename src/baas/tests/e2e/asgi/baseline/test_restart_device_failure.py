"""E2E tests for RESTART device failure: callback fails → device set to FAILED, re-restart recovers.

In the async hook (mock PaaS success) flow, the RESTART process:
1. _execute_restart_batch: ACTIVE → UPDATING → drain → restart_device()
   - restart_device(): destroy old container, create new container
   - The start phase dispatches an async callback
2. If callback returns FAILED → device set to FAILED, publish FAILED
3. FAILED device can be selected by next RESTART and recovered

This test verifies the complete cycle: callback FAILED → device FAILED → re-restart SUCCESS → device ACTIVE.

Note: PaaS destroy failure (PAAS_MOCK_DESTROY_FAILURE) does NOT trigger the exception
path in _execute_restart_batch because restart_device() swallows PaaS destroy errors
and continues to create. That path is covered by unit tests.

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


async def _get_device_statuses(api: APITestHelper, bot_uuid: str) -> list[str]:
    """Get device statuses for a bot via detail-by-uuid."""
    resp = await api.client.get(
        f"{api.bot_url(bot_uuid)}/detail-by-uuid",
        params=api.params(),
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    statuses: list[str] = []
    for item in items:
        for d in item.get("devices", []):
            statuses.append(d.get("status", "UNKNOWN"))
    return statuses


class TestRestartCallbackFailureRollback:
    """RESTART: callback FAILED → device FAILED → re-restart recovers."""

    @pytest.mark.asyncio
    async def test_restart_callback_fail_device_to_failed(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Callback FAILED: verify device is set to FAILED, publish FAILED.

        1. Create and activate bot (device = ACTIVE)
        2. RESTART → approve → send FAILED callback
        3. Verify device status = FAILED (not stuck in UPDATING)
        4. Verify publish status = FAILED
        """
        bot = await create_and_activate_bot(
            api, f"restart-cb-fail-{unique_id}", device_count=1
        )

        device_statuses_before = await _get_device_statuses(api, bot["bot_uuid"])
        assert "ACTIVE" in device_statuses_before, (
            f"Expected ACTIVE device, got {device_statuses_before}"
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
            stderr="restart callback failed",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", (
            f"Expected FAILED from callback failure, got {status}"
        )

        device_statuses = await _get_device_statuses(api, bot["bot_uuid"])
        assert device_statuses, "No devices found for bot"
        assert "FAILED" in device_statuses, (
            f"Expected device set to FAILED after callback failure, "
            f"got statuses: {device_statuses}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_restart_callback_fail_then_retry_recovers(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Callback FAILED → device FAILED → re-restart SUCCESS → device ACTIVE.

        1. RESTART with FAILED callback → device FAILED
        2. RESTART again (scope=all) with SUCCESS callback → FAILED device selected + recovered
        3. Verify device = ACTIVE after recovery
        """
        bot = await create_and_activate_bot(
            api, f"restart-cb-retry-{unique_id}", device_count=1
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
            stderr="first restart callback failed",
        )

        status_1 = await wait_for_publish_status(
            api, publish_id_1, {"FAILED"}, timeout_seconds=0.5
        )
        assert status_1 == "FAILED", f"First restart should FAIL, got {status_1}"

        device_statuses = await _get_device_statuses(api, bot["bot_uuid"])
        assert "FAILED" in device_statuses, (
            f"Expected device FAILED after callback failure, got {device_statuses}"
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
            f"Retry restart should pick up FAILED device, got {status_2}"
        )

        device_statuses = await _get_device_statuses(api, bot["bot_uuid"])
        assert "ACTIVE" in device_statuses, (
            f"Expected device ACTIVE after recovery restart, got {device_statuses}"
        )

        await cleanup_bot(api, bot["bot_uuid"])
