"""E2E tests for mock PaaS device creation failure.

When PAAS_MOCK_CREATE_FAILURE=true, MockPaasService.create_device()
raises PaasError(DEVICE_CREATION_FAILED).

This tests the synchronous failure path where PaaS cannot create devices.
Device status transitions: PENDING → FAILED. No hook is dispatched.

Behaviors to verify:
- CREATE: create_device fails → device FAILED, publish FAILED, bot FAILED
- Multi-device CREATE: all devices fail → publish FAILED
- SCALE_UP: creation fails for new devices → publish FAILED

Note: Since PAAS_MOCK_CREATE_FAILURE=true makes ALL create_device calls fail,
we cannot create+activate bots through the normal flow. Tests that need an
active bot first (scale_up) use a no-hook bot creation which goes through a
different code path — but since create_device itself fails, we test the
initial CREATE failure path only.

Requires:
- Service running with PAAS_MOCK_MODE=true + PAAS_MOCK_CREATE_FAILURE=true
  (just restart-mock-failure-create)
"""

import pytest

from ..conftest import APITestHelper, create_test_bot
from ..hook_helpers import (
    approve_publish,
    create_hook_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e, pytest.mark.mock_paas_create_failure]


# ── CREATE device failure ────────────────────────────────────────────────────


class TestCreateDeviceFailure:
    """create_device raises PaasError(DEVICE_CREATION_FAILED)."""

    @pytest.mark.asyncio
    async def test_create_device_fails(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """CREATE: approve → device creation fails → device FAILED, publish FAILED, bot FAILED."""
        bot = await create_test_bot(api, f"create-fail-{unique_id}")
        publish_id = bot["publish_id"]

        code = await approve_publish(api, publish_id)
        assert code == 200

        # Synchronous failure: create_device throws → device FAILED immediately
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        # Verify device result_status is FAILED
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            assert device.get("result_status") == "FAILED"

        # Verify bot is FAILED (CREATE failure propagates to PENDING bot)
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        if resp.status_code == 200:
            assert resp.json()["data"]["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_create_device_fails_multi_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Multi-device CREATE: all devices fail → publish FAILED."""
        bot = await create_test_bot(
            api, f"create-fail-multi-{unique_id}", device_count=3
        )
        publish_id = bot["publish_id"]

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        # All devices should have FAILED result_status
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            assert device.get("result_status") == "FAILED"


class TestCreateDeviceFailureWithHook:
    """create_device failure with after_create_cmd_hook configured.

    When create_device fails, the hook is never dispatched because
    the device never reaches the point where a hook would run.
    """

    @pytest.mark.asyncio
    async def test_create_with_hook_device_failure(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Hook bot, but create_device fails → no hook dispatched, device FAILED."""
        bot = await create_hook_bot(api, f"create-hook-devfail-{unique_id}")
        publish_id = bot["publish_id"]

        code = await approve_publish(api, publish_id)
        assert code == 200

        # Device creation fails synchronously → no CREATED devices waiting for callback
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        # Verify no CREATED devices (all should be FAILED directly)
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            # Device should be FAILED, not stuck in CREATED
            assert device.get("result_status") == "FAILED"
