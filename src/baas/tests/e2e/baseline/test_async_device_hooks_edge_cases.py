"""E2E tests for device callback endpoint edge cases.

Tests idempotency, 404 handling, and protocol-level behaviors
that apply across all publish types.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import pytest

from ..conftest import APITestHelper, call_device_callback, cleanup_bot
from ..hook_helpers import (
    approve_and_complete,
    create_hook_bot,
    get_devices_from_progress,
    get_publish_status,
)

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestCallbackEdgeCases:
    """Callback endpoint edge cases across publish types."""

    @pytest.mark.asyncio
    async def test_callback_unknown_device_404(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Callback with non-existent device_uuid → 404."""
        resp = await call_device_callback(
            api,
            device_uuid="DEVICE-nonexistent123",
            publish_id=99999,
            result_status="SUCCESS",
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_duplicate_callback_ignored(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """After SUCCESS, duplicate FAILED callback is idempotently ignored."""
        bot = await create_hook_bot(api, f"edge-idempotent-{unique_id}")
        publish_id = bot["publish_id"]

        await approve_and_complete(api, publish_id)

        devices = await get_devices_from_progress(api, publish_id)
        if not devices or not devices[0].get("device_uuid"):
            await cleanup_bot(api, bot["bot_uuid"])
            pytest.skip("No device found in progress")

        device_uuid = devices[0]["device_uuid"]

        resp = await call_device_callback(
            api,
            device_uuid=device_uuid,
            publish_id=publish_id,
            result_status="FAILED",
            exit_code=1,
            stderr="duplicate callback",
        )
        assert resp.status_code == 200

        current_status = await get_publish_status(api, publish_id)
        assert current_status in ("SUCCESS", "APPROVING"), (
            f"Idempotency broken: FAILED callback changed status to {current_status}"
        )

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_callback_with_stderr_captured(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Callback with non-empty stderr but SUCCESS result → still succeeds."""
        from ..hook_helpers import (
            approve_publish,
            assert_result_message_has_hook_data,
            send_callbacks_for_hook_devices,
            wait_for_publish_status,
        )

        bot = await create_hook_bot(api, f"edge-stderr-{unique_id}", device_count=1)
        publish_id = bot["publish_id"]

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="SUCCESS",
            exit_code=0,
            stdout="ok",
            stderr="warning: deprecation notice",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        devices = await get_devices_from_progress(api, publish_id)
        hook_data = assert_result_message_has_hook_data(
            devices[0].get("result_message")
        )
        assert "warning" in hook_data.get("stderr", "")

        await cleanup_bot(api, bot["bot_uuid"])
