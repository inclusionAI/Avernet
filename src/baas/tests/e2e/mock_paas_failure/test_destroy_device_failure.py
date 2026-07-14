"""E2E tests for mock PaaS device destroy failure.

When PAAS_MOCK_DESTROY_FAILURE=true, MockPaasService.destroy_device()
raises PaasError(DEVICE_DESTROY_FAILED).

This tests the destroy failure path where PaaS cannot destroy devices.

Key behaviors (per D-04):
- PaaS destroy failure: device status still transitions to RELEASED in DB (cleanup proceeds)
- Publish goes to FAILED, but DESTROY is terminal — bot → RELEASED + soft-deleted
- Device DB status → RELEASED (cleanup proceeds even on PaaS failure)
- DEVICE_NOT_FOUND is tested separately (idempotent success)

Note: Since PAAS_MOCK_DESTROY_FAILURE makes ALL destroy_device calls fail,
we cannot destroy bots for cleanup. Tests create+activate bots (which works
since create_device succeeds), then test the destroy failure path.

Requires:
- Service running with PAAS_MOCK_MODE=true + PAAS_MOCK_DESTROY_FAILURE=true
  (just restart-mock-failure-destroy)
"""

import uuid

import pytest

from ..conftest import (
    APITestHelper,
    create_test_bot,
)
from ..hook_helpers import (
    activate_bot,
    approve_and_complete,
    approve_publish,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e, pytest.mark.mock_paas_destroy_failure]


# ── DESTROY device failure ──────────────────────────────────────────────────


class TestDestroyDeviceFailure:
    """destroy_device raises PaasError(DEVICE_DESTROY_FAILED)."""

    @pytest.mark.asyncio
    async def test_destroy_device_fails(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """DESTROY: approve → PaaS destroy fails → publish FAILED, bot RELEASED, device RELEASED."""
        bot = await create_test_bot(api, f"destroy-fail-{unique_id}", device_count=1)
        await activate_bot(api, bot)

        # Destroy the bot
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/destroy",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200
        publish_id = resp.json()["data"].get("publish_id")
        if not publish_id:
            pytest.skip("No publish_id returned from destroy")

        code = await approve_publish(api, publish_id)
        assert code == 200

        # PaaS destroy fails → publish FAILED
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        # Verify device result_status is FAILED
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            assert device.get("result_status") == "FAILED"

        # After DESTROY failure, bot should be RELEASED + soft-deleted → 404
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        assert resp.status_code == 404, (
            f"Expected bot soft-deleted (404), got {resp.status_code}"
        )

        # Device status should be RELEASED + soft-deleted
        device_resp = await api.client.get(
            f"/api/v1/bots/{bot['bot_uuid']}/devices", params=api.params()
        )
        if device_resp.status_code != 404:
            data = device_resp.json().get("data") or {}
            device_list = data.get("items") or []
            for d in device_list:
                assert d.get("status") == "RELEASED", (
                    f"Expected device RELEASED, got {d.get('status')}"
                )

    @pytest.mark.asyncio
    async def test_destroy_device_fails_multi_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Multi-device DESTROY: all destroys fail → publish FAILED."""
        bot = await create_test_bot(
            api, f"destroy-fail-multi-{unique_id}", device_count=2
        )
        await activate_bot(api, bot)

        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/destroy",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200
        publish_id = resp.json()["data"].get("publish_id")
        if not publish_id:
            pytest.skip("No publish_id returned from destroy")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"


# ── SCALE_DOWN destroy failure ──────────────────────────────────────────────


class TestScaleDownDestroyFailure:
    """SCALE_DOWN: destroy_device fails for scaled-down devices."""

    @pytest.mark.asyncio
    async def test_scale_down_destroy_fails(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Scale down: PaaS destroy fails → publish FAILED."""
        bot = await create_test_bot(api, f"scaledn-fail-{unique_id}", device_count=3)
        await activate_bot(api, bot)

        # Scale down from 3 to 1
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/scale",
            params=api.params(),
            json={
                "target_count": 1,
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )
        assert resp.status_code == 200
        publish_id = resp.json()["data"].get("publish_id")
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"


# ── RESTART destroy phase failure ───────────────────────────────────────────


class TestRestartDestroyPhaseFailure:
    """RESTART: destroy_device fails during restart destroy phase.

    Per D-03: restart_device() continues past destroy failures.
    The create phase still runs and succeeds (PAAS_MOCK_CREATE_FAILURE not set),
    so the restart publish ultimately succeeds despite the destroy error.
    """

    @pytest.mark.asyncio
    async def test_restart_destroy_phase_fails_but_create_succeeds(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Restart: PaaS destroy fails but create succeeds → publish SUCCESS."""
        bot = await create_test_bot(
            api, f"restart-destroy-fail-{unique_id}", device_count=1
        )
        await activate_bot(api, bot)

        # Restart the bot (involves destroy + create phases)
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/restart",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200
        publish_id = resp.json()["data"].get("publish_id")
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        # Restart is multi-stage: use approve_and_complete to handle
        # all stage approvals + callback cycles until terminal state
        status = await approve_and_complete(api, publish_id)

        # Destroy fails but restart_device continues past it (per D-03)
        # Create phase succeeds → publish SUCCESS
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"
