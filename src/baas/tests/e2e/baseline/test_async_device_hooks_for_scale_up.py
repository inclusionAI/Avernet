"""E2E tests for async device hook callback flow: SCALE_UP publish type.

SCALE_UP uses after_create_cmd_hook (async, callback-driven).
Single batch, no stage gates — auto-complete when all callbacks received.

Requires:
- Service running with PAAS_MOCK_MODE=true (just restart-mock)
"""

import uuid

import pytest

from ..conftest import APITestHelper, cleanup_bot
from ..hook_helpers import (
    approve_publish,
    create_and_activate_bot,
    send_callbacks_for_hook_devices,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


async def _scale_up_bot(
    api: APITestHelper, bot_uuid: str, target_count: int
) -> int | None:
    """Scale up bot, return publish_id or None."""
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


class TestScaleUpSuccess:
    """SCALE_UP with hook: approve → callback SUCCESS → publish SUCCESS."""

    @pytest.mark.asyncio
    async def test_scale_up_1_device_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1→2 devices: approve → callback → SUCCESS."""
        bot = await create_and_activate_bot(
            api, f"scaleup-hook-1d-{unique_id}", device_count=1
        )
        publish_id = await _scale_up_bot(api, bot["bot_uuid"], 2)
        if not publish_id:
            pytest.skip("No publish_id returned from scale up")

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

        # Verify device count via detail-by-uuid
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
        assert len(active_devices) >= 2

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_scale_up_2_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1→3 devices: approve → callback for each → SUCCESS."""
        bot = await create_and_activate_bot(
            api, f"scaleup-hook-2d-{unique_id}", device_count=1
        )
        publish_id = await _scale_up_bot(api, bot["bot_uuid"], 3)
        if not publish_id:
            pytest.skip("No publish_id returned from scale up")

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_callbacks_for_hook_devices(api, publish_id)

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_scale_up_3_devices_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1→4 devices: approve → callback for each → SUCCESS."""
        bot = await create_and_activate_bot(
            api, f"scaleup-hook-3d-{unique_id}", device_count=1
        )
        publish_id = await _scale_up_bot(api, bot["bot_uuid"], 4)
        if not publish_id:
            pytest.skip("No publish_id returned from scale up")

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_callbacks_for_hook_devices(api, publish_id)

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])


# ── Failure path ─────────────────────────────────────────────────────────────


class TestScaleUpFailure:
    """SCALE_UP with hook: FAILED callback → publish FAILED."""

    @pytest.mark.asyncio
    async def test_scale_up_1_device_hook_failure(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1→2 devices: approve → FAILED callback → publish FAILED."""
        bot = await create_and_activate_bot(
            api, f"scaleup-fail-1d-{unique_id}", device_count=1
        )
        publish_id = await _scale_up_bot(api, bot["bot_uuid"], 2)
        if not publish_id:
            pytest.skip("No publish_id returned from scale up")

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_callbacks_for_hook_devices(
            api,
            publish_id,
            result_status="FAILED",
            exit_code=1,
            stderr="scale-up hook failed",
        )

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_scale_up_2_devices_one_fails(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1→3 devices: approve → mixed callbacks (1 SUCCESS + 1 FAILED) → FAILED."""
        from ..hook_helpers import send_mixed_callbacks

        bot = await create_and_activate_bot(
            api, f"scaleup-mixed-{unique_id}", device_count=1
        )
        publish_id = await _scale_up_bot(api, bot["bot_uuid"], 3)
        if not publish_id:
            pytest.skip("No publish_id returned from scale up")

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_mixed_callbacks(api, publish_id, fail_index=1)

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_scale_up_3_devices_one_fails(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """1→4 devices: approve → FAILED on one device → batch FAILED."""
        from ..hook_helpers import send_mixed_callbacks

        bot = await create_and_activate_bot(
            api, f"scaleup-3d-fail-{unique_id}", device_count=1
        )
        publish_id = await _scale_up_bot(api, bot["bot_uuid"], 4)
        if not publish_id:
            pytest.skip("No publish_id returned from scale up")

        code = await approve_publish(api, publish_id)
        assert code == 200

        await send_mixed_callbacks(api, publish_id, fail_index=0)

        status = await wait_for_publish_status(
            api, publish_id, {"FAILED"}, timeout_seconds=0.5
        )
        assert status == "FAILED", f"Expected FAILED, got {status}"

        await cleanup_bot(api, bot["bot_uuid"])
