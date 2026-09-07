"""E2E tests for the TeClaw async-callback edge cases.

Covers idempotent double call (publish_record PROCESSING guard) and failure
callback (device -> FAILED, publish_record -> FAILED).

Removed (no longer applicable in the router-level conversion architecture):
- Stale-task discard: task_id is ignored by the router.
- Destroy -> STOPPED: DELETE is sync-only; async callback never receives DELETE.
- Error envelope persistence: provider_device_props.callback_payload is gone.

Requires ASGI TestClient transport (it-sqlite overlay).
"""

from typing import TYPE_CHECKING

import pytest

from tests.e2e.asgi.baseline.test_teclaw_callback_happy_path import (
    _callback_payload,
    _find_processing_device,
    _get_device,
)
from tests.e2e.asgi.conftest import (
    APITestHelper,
    approve_publish,
    call_teclaw_callback,
    cleanup_bot,
    create_hook_bot,
    get_devices_from_progress,
)

if TYPE_CHECKING:
    from secbaas.community.bootstrap import ApplicationContainer

pytestmark = [pytest.mark.e2e_asgi]


class TestTeclawCallbackIdempotency:
    """Idempotency via the publish_record PROCESSING guard."""

    @pytest.mark.asyncio
    async def test_teclaw_callback_idempotent_double_call(
        self,
        api: APITestHelper,
        bootstrap_init: "ApplicationContainer",
        unique_id: str,
    ) -> None:
        """First SUCCESS call: record PROCESSING -> SUCCESS, returns
        {"status": "processed"}. Second call: record already SUCCESS (not
        PROCESSING), returns {"status": "ignored", "reason":
        "no PROCESSING record found"}."""
        bot = await create_hook_bot(api, f"teclaw-idem-{unique_id}")
        publish_id = bot["publish_id"]
        try:
            code = await approve_publish(api, publish_id)
            assert code == 200, f"Approve failed: {code}"

            device = await _find_processing_device(api, publish_id)
            assert device is not None, (
                f"No PROCESSING device found for publish_id={publish_id}"
            )
            device_uuid = device["device_uuid"]

            payload = _callback_payload(
                device_uuid, publish_id, api.tenant, success=True,
            )

            code1, body1 = await call_teclaw_callback(api.client, payload)
            assert code1 == 200, f"First call failed: {code1} {body1}"
            assert body1["data"]["status"] == "processed", (
                f"First call should return 'processed'; got: {body1}"
            )

            code2, body2 = await call_teclaw_callback(api.client, payload)
            assert code2 == 200, f"Second call failed: {code2} {body2}"
            assert body2["data"]["status"] == "ignored", (
                f"Second call should return 'ignored'; got: {body2}"
            )
            assert body2["data"]["reason"] == "no PROCESSING record found", (
                f"Second call reason mismatch; got: {body2}"
            )

            db_device = _get_device(bootstrap_init, device_uuid)
            assert db_device is not None, "Device not found after callbacks"
            assert db_device.status == "ACTIVE", (
                f"Device should be ACTIVE after idempotent callbacks; "
                f"got: {db_device.status}"
            )
        finally:
            await cleanup_bot(api, bot["bot_uuid"])


class TestTeclawCallbackFailurePersistence:
    """Failure callback transitions device -> FAILED and publish_record -> FAILED."""

    @pytest.mark.asyncio
    async def test_teclaw_callback_failure_persists_error_envelope(
        self,
        api: APITestHelper,
        bootstrap_init: "ApplicationContainer",
        unique_id: str,
    ) -> None:
        """Failure callback (success=false) -> device status FAILED and
        publish_record result_status FAILED. The error string is passed as
        stderr to DeviceCallbackRequest but is no longer persisted in
        provider_device_props (that concept was removed)."""
        bot = await create_hook_bot(api, f"teclaw-fail-{unique_id}")
        publish_id = bot["publish_id"]
        try:
            code = await approve_publish(api, publish_id)
            assert code == 200, f"Approve failed: {code}"

            device = await _find_processing_device(api, publish_id)
            assert device is not None, (
                f"No PROCESSING device found for publish_id={publish_id}"
            )
            device_uuid = device["device_uuid"]

            # Phase 1 assertion: after async submit, before failure callback.
            # publish_record stays PROCESSING and device stays PENDING.
            pre_device = _get_device(bootstrap_init, device_uuid)
            assert pre_device is not None, "Device not found pre-callback"
            assert pre_device.status == "PENDING", (
                f"Device should be PENDING pre-callback; got: {pre_device.status}"
            )
            assert device.get("result_status") == "PROCESSING", (
                f"publish_record should be PROCESSING pre-callback; "
                f"got: {device.get('result_status')}"
            )

            payload = _callback_payload(
                device_uuid,
                publish_id,
                api.tenant,
                success=False,
                error="boom",
                error_code="E_INTERNAL",
            )
            status_code, body = await call_teclaw_callback(api.client, payload)

            assert status_code == 200, f"Expected 200, got {status_code}: {body}"
            assert body["data"]["status"] == "processed", (
                f"Expected 'processed'; got: {body}"
            )

            # Phase 2 assertion: after failure callback.
            # device PENDING -> FAILED, publish_record PROCESSING -> FAILED.
            db_device = _get_device(bootstrap_init, device_uuid)
            assert db_device is not None, "Device not found after failure callback"
            assert db_device.status == "FAILED", (
                f"Failure callback should transition device to FAILED; "
                f"got: {db_device.status}"
            )

            devices = await get_devices_from_progress(api, publish_id)
            target = next(
                (d for d in devices if d.get("device_uuid") == device_uuid),
                None,
            )
            assert target is not None, (
                "Device not found in progress after failure callback"
            )
            assert target.get("result_status") == "FAILED", (
                f"publish_record result_status should be 'FAILED'; "
                f"got: {target.get('result_status')}"
            )
        finally:
            await cleanup_bot(api, bot["bot_uuid"])