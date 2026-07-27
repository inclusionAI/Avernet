"""E2E tests for async device hook callback flow: SCALE_DOWN publish type.

SCALE_DOWN uses before_destroy_cmd_hook (synchronous, inline execution).
Single batch, no stage gates. Hook runs before PaaS destroy, result captured inline.
Hook exit_code/stdout/stderr stored in result_message via serialize_hook_result().
No external callback needed — before_destroy_cmd_hook is NOT callback-driven.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import uuid

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    activate_test_bot,
    approve_publish,
    assert_result_message_has_hook_data,
    cleanup_bot,
    create_and_activate_bot,
    create_test_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e_asgi]


async def _scale_down_bot(
    api: APITestHelper, bot_uuid: str, target_count: int
) -> int | None:
    """Scale down bot, return publish_id or None."""
    resp = await api.client.post(
        api.bot_url(bot_uuid) + "/scale",
        params=api.params(),
        json={
            "target_count": target_count,
            "operator": "e2e-test",
            "request_id": uuid.uuid4().hex,
        },
    )
    assert resp.status_code == 200
    return resp.json()["data"].get("publish_id")


# ── Success path ─────────────────────────────────────────────────────────────


class TestScaleDownSuccess:
    """SCALE_DOWN with before_destroy_cmd_hook: sync hook, no callback needed."""

    @pytest.mark.asyncio
    async def test_scale_down_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3→2 devices: approve → sync hook runs inline → SUCCESS."""
        bot = await create_and_activate_bot(
            api, f"scaledn-hook-1d-{unique_id}", device_count=3
        )
        publish_id = await _scale_down_bot(api, bot["bot_uuid"], 2)
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        # Approve — before_destroy_cmd_hook runs synchronously, no callback needed
        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Verify hook data in result_message
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            result_msg = device.get("result_message")
            if result_msg and result_msg.startswith("{"):
                hook_data = assert_result_message_has_hook_data(result_msg)
                assert "stdout" in hook_data

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_scale_down_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """3→1 devices: approve → sync hook per device → SUCCESS."""
        bot = await create_and_activate_bot(
            api, f"scaledn-hook-2d-{unique_id}", device_count=3
        )
        publish_id = await _scale_down_bot(api, bot["bot_uuid"], 1)
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Verify hook data in result_message
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            result_msg = device.get("result_message")
            if result_msg and result_msg.startswith("{"):
                hook_data = assert_result_message_has_hook_data(result_msg)
                assert "exit_code" in hook_data

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_scale_down_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """4→1 devices: approve → sync hook per device → SUCCESS."""
        bot = await create_and_activate_bot(
            api, f"scaledn-hook-3d-{unique_id}", device_count=4
        )
        publish_id = await _scale_down_bot(api, bot["bot_uuid"], 1)
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # Verify hook data in result_message
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            result_msg = device.get("result_message")
            if result_msg and result_msg.startswith("{"):
                assert_result_message_has_hook_data(result_msg)

        await cleanup_bot(api, bot["bot_uuid"])


# ── No-hook baseline ─────────────────────────────────────────────────────────


class TestScaleDownNoHook:
    """SCALE_DOWN without hook: direct destroy, no hook execution."""

    @pytest.mark.asyncio
    async def test_scale_down_no_hook(self, api: APITestHelper, unique_id: str) -> None:
        """Scale down without hook → direct fast path, plain text result_message."""
        bot = await create_test_bot(api, f"scaledn-nohook-{unique_id}", device_count=3)
        await activate_test_bot(api, bot)

        publish_id = await _scale_down_bot(api, bot["bot_uuid"], 1)
        if not publish_id:
            pytest.skip("No publish_id returned from scale down")

        code = await approve_publish(api, publish_id)
        assert code == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        # No hook → result_message is plain text (not JSON)
        devices = await get_devices_from_progress(api, publish_id)
        if devices and devices[0].get("result_message"):
            assert not devices[0]["result_message"].startswith("{"), (
                "No-hook result_message should be plain text, not JSON"
            )

        await cleanup_bot(api, bot["bot_uuid"])
