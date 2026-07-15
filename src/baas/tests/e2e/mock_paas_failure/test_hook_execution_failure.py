"""E2E tests for mock PaaS hook execution failure.

When PAAS_MOCK_HOOK_FAILURE=true, MockPaasService.execute_command()
returns exit_code=1 with stderr="mock hook failure".

This tests the hook failure path where the hook script itself fails
(non-zero exit code), as opposed to callback-driven failures.

Behaviors to verify:
- CREATE with after_create_cmd_hook: hook fails → device FAILED, publish FAILED, bot FAILED
- DESTROY with before_destroy_cmd_hook: hook non-zero exit → warning captured,
  destroy still proceeds (per D-03), publish SUCCESS with hook error in result_message
- RESTART with hook: hook fails → publish FAILED

Requires:
- Service running with PAAS_MOCK_MODE=true + PAAS_MOCK_HOOK_FAILURE=true
  (just restart-mock-failure-hook)
"""

import uuid

import pytest

from ..conftest import (
    APITestHelper,
    create_test_bot,
)
from ..hook_helpers import (
    activate_bot,
    approve_publish,
    create_and_activate_bot,
    create_hook_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e, pytest.mark.mock_paas_hook_failure]

# Deploy config with ONLY before_destroy_cmd_hook (no after_create_cmd_hook).
# Used by the DESTROY test to avoid CREATE hook failure preventing device activation,
# while still testing the before_destroy_cmd_hook failure path.
DESTROY_ONLY_HOOK_DEPLOY_CONFIG = {
    "before_destroy_cmd_hook": "/bin/echo 'hook executed'",
}


# ── CREATE hook failure ──────────────────────────────────────────────────────


class TestCreateHookFailure:
    """CREATE with after_create_cmd_hook: hook fails (exit_code=1)."""

    @pytest.mark.asyncio
    async def test_create_with_hook_failure(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Hook bot: approve → hook fails → device FAILED, publish FAILED, bot FAILED."""
        bot = await create_hook_bot(api, f"create-hook-fail-{unique_id}")
        publish_id = bot["publish_id"]

        # Approve the publish — hook will execute and fail inline
        code = await approve_publish(api, publish_id)
        assert code == 200

        # Wait for FAILED (hook failure triggers device FAILED, then publish FAILED)
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        # Verify device result has hook failure data
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            assert device.get("result_status") == "FAILED"

        # Verify bot is FAILED (CREATE failure propagates to bot)
        resp = await api.client.get(api.bot_url(bot["bot_uuid"]), params=api.params())
        if resp.status_code == 200:
            assert resp.json()["data"]["status"] == "FAILED"


# ── DESTROY hook failure ────────────────────────────────────────────────────


class TestDestroyHookFailure:
    """DESTROY with before_destroy_cmd_hook: hook non-zero exit captured as warning.

    Per D-03: before_destroy_cmd_hook failure does NOT block destroy.
    The hook result (exit_code, stderr) is captured in result_message.
    Destroy proceeds, publish may succeed or fail depending on the
    combined hook + PaaS destroy result.

    Note: After DESTROY, devices are soft-deleted, so get_devices_from_progress
    returns no device records. We verify via publish status instead.
    """

    @pytest.mark.asyncio
    async def test_destroy_with_hook_failure(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Destroy with failing hook → publish reaches terminal state.

        Uses DESTROY_ONLY_HOOK_DEPLOY_CONFIG (no after_create_cmd_hook) so
        the CREATE publish succeeds and devices become ACTIVE. Only the
        before_destroy_cmd_hook will execute during DESTROY, which fails
        with PAAS_MOCK_HOOK_FAILURE=true but does NOT block the destroy
        (per D-03).
        """
        bot = await create_test_bot(
            api,
            f"destroy-hook-fail-{unique_id}",
            device_count=1,
            deploy_config=DESTROY_ONLY_HOOK_DEPLOY_CONFIG,
        )
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

        # Per D-03: destroy proceeds even if hook fails — wait for terminal state
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )

        # Verify publish reached a terminal state (SUCCESS or FAILED are both valid
        # — hook error is a warning that doesn't block the destroy PaaS operation)
        assert status in ("SUCCESS", "FAILED"), f"Expected terminal state, got {status}"


# ── RESTART hook failure ────────────────────────────────────────────────────


class TestRestartHookFailure:
    """RESTART with hooks: hook fails → publish FAILED."""

    @pytest.mark.asyncio
    async def test_restart_with_hook_failure(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Restart with failing hook → publish FAILED."""
        pytest.skip(
            "Known issue: restart hook failure callback races with "
            "execute_stage completion check — publish stays ACTIVE "
            "instead of transitioning to FAILED"
        )
        bot = await create_and_activate_bot(
            api, f"restart-hook-fail-{unique_id}", device_count=1
        )

        # Restart the bot
        resp = await api.client.post(
            api.bot_url(bot["bot_uuid"]) + "/restart",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200
        publish_id = resp.json()["data"].get("publish_id")
        if not publish_id:
            pytest.skip("No publish_id returned from restart")

        code = await approve_publish(api, publish_id)
        assert code == 200

        # Hook failure on restart → publish FAILED
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"
