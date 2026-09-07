"""E2E test exercising the TeClaw async CREATE path through the publish flow.

Unlike happy_path/edge_cases tests (which use ARCA template + hook), this
test creates a bot with TEMPLATE_TECLAW so that ``start_device`` enters the
``elif provider_type == "TECLAW":`` branch, builds ``TeClawDeviceConfig`` with
``callback_context`` set, calls the stub TeClaw plugin, and then exercises
the Step 8 early-return at ``_device_service.py:1122-1136`` that keeps the
device PENDING until the external callback arrives.

Phases asserted:
  1. Pre-callback:  device=PENDING      publish_record=PROCESSING
  2. Post-success:  device=ACTIVE        publish_record=SUCCESS
  3. Post-failure:  device=FAILED        publish_record=FAILED (separate bot)

Requires ASGI TestClient transport (it-sqlite overlay).
"""

from typing import TYPE_CHECKING, Any

import pytest

from tests.e2e.asgi.baseline.test_teclaw_callback_happy_path import (
    _callback_payload,
    _find_processing_device,
    _get_device,
)
from tests.e2e.asgi.conftest import (
    APITestHelper,
    TEMPLATE_TECLAW,
    approve_publish,
    call_teclaw_callback,
    cleanup_bot,
    create_test_bot,
    get_devices_from_progress,
)

if TYPE_CHECKING:
    from secbaas.community.bootstrap import ApplicationContainer

pytestmark = [pytest.mark.e2e_asgi]


async def _create_teclaw_bot(
    api: APITestHelper, name: str
) -> dict[str, Any]:
    return await create_test_bot(
        api,
        name,
        template_uuid=TEMPLATE_TECLAW,
    )


class TestTeClawAsyncPublishFlowSuccess:

    @pytest.mark.asyncio
    async def test_teclaw_async_publish_success(
        self,
        api: APITestHelper,
        bootstrap_init: "ApplicationContainer",
        unique_id: str,
    ) -> None:
        bot = await _create_teclaw_bot(api, f"teclaw-async-ok-{unique_id}")
        publish_id = bot["publish_id"]
        try:
            code = await approve_publish(api, publish_id)
            assert code == 200, f"Approve failed: {code}"

            device = await _find_processing_device(api, publish_id)
            assert device is not None, (
                f"No PROCESSING device found for publish_id={publish_id} "
                f"(TeClaw async path may not have engaged)"
            )
            device_uuid = device["device_uuid"]

            # Phase 1: pre-callback — Step 8 fix fires for TeClaw
            pre_device = _get_device(bootstrap_init, device_uuid)
            assert pre_device is not None, "Device not found pre-callback"
            assert pre_device.provider_type == "TECLAW", (
                f"Expected provider_type=TECLAW for TEMPLATE_TECLAW bot; "
                f"got: {pre_device.provider_type}"
            )
            assert pre_device.status == "PENDING", (
                f"TeClaw async device should be PENDING pre-callback; "
                f"got: {pre_device.status}"
            )
            assert device.get("result_status") == "PROCESSING", (
                f"TeClaw async publish_record should be PROCESSING pre-callback; "
                f"got: {device.get('result_status')}"
            )

            payload = _callback_payload(
                device_uuid, publish_id, api.tenant, success=True,
            )
            status_code, body = await call_teclaw_callback(api.client, payload)
            assert status_code == 200, f"Callback failed: {status_code} {body}"
            assert body["code"] == 0, f"Callback code!=0: {body}"
            assert body["data"]["status"] == "processed", (
                f"Callback should return 'processed'; got: {body}"
            )

            # Phase 2: post-success callback
            post_device = _get_device(bootstrap_init, device_uuid)
            assert post_device is not None, "Device not found after callback"
            assert post_device.status == "ACTIVE", (
                f"TeClaw async device should be ACTIVE after success callback; "
                f"got: {post_device.status}"
            )

            devices = await get_devices_from_progress(api, publish_id)
            target = next(
                (d for d in devices if d.get("device_uuid") == device_uuid),
                None,
            )
            assert target is not None, "Device missing from progress post-callback"
            assert target.get("result_status") == "SUCCESS", (
                f"TeClaw async publish_record should be SUCCESS post-callback; "
                f"got: {target.get('result_status')}"
            )
        finally:
            await cleanup_bot(api, bot["bot_uuid"])


class TestTeClawAsyncPublishFlowFailure:

    @pytest.mark.asyncio
    async def test_teclaw_async_publish_failure(
        self,
        api: APITestHelper,
        bootstrap_init: "ApplicationContainer",
        unique_id: str,
    ) -> None:
        bot = await _create_teclaw_bot(api, f"teclaw-async-fail-{unique_id}")
        publish_id = bot["publish_id"]
        try:
            code = await approve_publish(api, publish_id)
            assert code == 200, f"Approve failed: {code}"

            device = await _find_processing_device(api, publish_id)
            assert device is not None, (
                f"No PROCESSING device found for publish_id={publish_id}"
            )
            device_uuid = device["device_uuid"]

            # Phase 1: pre-callback
            pre_device = _get_device(bootstrap_init, device_uuid)
            assert pre_device is not None, "Device not found pre-callback"
            assert pre_device.provider_type == "TECLAW", (
                f"Expected provider_type=TECLAW; got: {pre_device.provider_type}"
            )
            assert pre_device.status == "PENDING", (
                f"TeClaw async device should be PENDING pre-callback; "
                f"got: {pre_device.status}"
            )
            assert device.get("result_status") == "PROCESSING", (
                f"TeClaw async publish_record should be PROCESSING pre-callback; "
                f"got: {device.get('result_status')}"
            )

            payload = _callback_payload(
                device_uuid,
                publish_id,
                api.tenant,
                success=False,
                error="teclaw async create failed",
                error_code="CREATE_FAILED",
            )
            status_code, body = await call_teclaw_callback(api.client, payload)
            assert status_code == 200, f"Callback failed: {status_code} {body}"

            # Phase 2: post-failure callback
            post_device = _get_device(bootstrap_init, device_uuid)
            assert post_device is not None, "Device not found after callback"
            assert post_device.status == "FAILED", (
                f"TeClaw async device should be FAILED after failure callback; "
                f"got: {post_device.status}"
            )

            devices = await get_devices_from_progress(api, publish_id)
            target = next(
                (d for d in devices if d.get("device_uuid") == device_uuid),
                None,
            )
            assert target is not None, "Device missing from progress post-callback"
            assert target.get("result_status") == "FAILED", (
                f"TeClaw async publish_record should be FAILED post-callback; "
                f"got: {target.get('result_status')}"
            )
        finally:
            await cleanup_bot(api, bot["bot_uuid"])