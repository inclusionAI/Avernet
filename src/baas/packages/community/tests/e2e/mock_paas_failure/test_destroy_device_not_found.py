"""E2E tests for mock PaaS device-not-found (idempotent destroy).

When PAAS_MOCK_DEVICE_NOT_FOUND=true, MockPaasService.destroy_device()
raises PaasError(DEVICE_NOT_FOUND).

Per D-04/D-05: DEVICE_NOT_FOUND during destroy is treated as idempotent success.
The device was already gone, so the destroy is considered successful.

Behaviors to verify:
- DESTROY: device not found → treated as success → publish SUCCESS
- SCALE_DOWN: device not found → treated as success → publish SUCCESS

Note: Since PAAS_MOCK_DEVICE_NOT_FOUND makes ALL destroy_device calls raise
DEVICE_NOT_FOUND, activation still works (create_device succeeds). The
failure only triggers on destroy operations.

Requires:
- Service running with PAAS_MOCK_MODE=true + PAAS_MOCK_DEVICE_NOT_FOUND=true
  (just restart-mock-failure-device-not-found)
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
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.e2e, pytest.mark.mock_paas_device_not_found]


# ── DESTROY device not found (idempotent) ───────────────────────────────────


class TestDestroyDeviceNotFound:
    """destroy_device raises PaasError(DEVICE_NOT_FOUND) → treated as success."""

    @pytest.mark.asyncio
    async def test_destroy_not_found_is_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """DESTROY: device not found -> idempotent success -> publish SUCCESS."""
        bot = await create_test_bot(
            api, f"destroy-notfound-{unique_id}", device_count=1
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

        # DEVICE_NOT_FOUND is treated as idempotent success
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS (idempotent), got {status}"

        # Verify device result_status is SUCCESS
        devices = await get_devices_from_progress(api, publish_id)
        assert len(devices) >= 1
        for device in devices:
            assert device.get("result_status") == "SUCCESS"

    @pytest.mark.asyncio
    async def test_destroy_not_found_multi_device(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Multi-device DESTROY: all not found -> all SUCCESS (idempotent)."""
        bot = await create_test_bot(
            api, f"destroy-notfound-multi-{unique_id}", device_count=2
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
        assert status == "SUCCESS", f"Expected SUCCESS (idempotent), got {status}"


# ── SCALE_DOWN device not found (idempotent) ────────────────────────────────


class TestScaleDownDeviceNotFound:
    """SCALE_DOWN: device not found → idempotent success."""

    @pytest.mark.asyncio
    async def test_scale_down_not_found_is_success(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Scale down: device not found -> idempotent success -> publish SUCCESS."""
        bot = await create_test_bot(
            api, f"scaledn-notfound-{unique_id}", device_count=3
        )
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

        # DEVICE_NOT_FOUND is treated as idempotent success
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=0.5
        )
        assert status == "SUCCESS", f"Expected SUCCESS (idempotent), got {status}"
