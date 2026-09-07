"""E2E tests for the TeClaw async-callback happy-path flow.

The TeClaw callback envelope is converted to a DeviceCallbackRequest in the
router and delegates to publish_service.handle_device_callback. A PROCESSING
publish_record is created via the standard publish approve flow with a hook
deploy config.

Requires ASGI TestClient transport (it-sqlite overlay).
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any

import pytest

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


def _seed_teclaw_device(
    bootstrap_init: "ApplicationContainer",
    *,
    device_uuid: str,
    status: str = "PENDING",
    task_id: str = "t-1",
    task_status: str = "RUNNING",
    operation: str = "CREATE",
    version: int = 1,
    provider_type: str = "TECLAW",
) -> int:
    """Insert a device row with TeClaw async-task props. Returns the record id."""
    device_repo = bootstrap_init.repository.device_repository()
    return device_repo.insert_device(
        device_uuid=device_uuid,
        tenant="team_claw",
        env="local",
        domain="teclaw-test",
        creator="e2e-seed",
        modifier="e2e-seed",
        status=status,
        provider_type=provider_type,
        provider_device_id=f"teclaw-prov-{device_uuid}",
        provider_device_props={
            "task_id": task_id,
            "task_status": task_status,
            "operation": operation,
            "version": version,
        },
        extra_config={},
    )


def _get_device(bootstrap_init: "ApplicationContainer", device_uuid: str) -> Any:
    """Read back a device record by device_uuid (cross-tenant lookup)."""
    device_repo = bootstrap_init.repository.device_repository()
    return device_repo.get_by_device_uuid_only(device_uuid)


def _callback_payload(
    device_uuid: str,
    publish_id: int,
    tenant: str,
    *,
    success: bool = True,
    error: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    """Build a TeclawCallbackRequest-shaped payload.

    publish_id is passed as a string in callback_context; the router converts
    it to int. task_id/operation/task_status are required by the Pydantic
    model but ignored by the router conversion logic.
    """
    payload: dict[str, Any] = {
        "success": success,
        "data": {
            "schema_version": 1,
            "callback_context": {
                "device_uuid": device_uuid,
                "publish_id": str(publish_id),
                "tenant": tenant,
            },
            "task_id": "t-1",
            "operation": "CREATE",
            "task_status": "SUCCESS" if success else "FAILED",
            "bot_id": "b-1",
            "version": 1,
        },
    }
    if error is not None:
        payload["error"] = error
    if error_code is not None:
        payload["error_code"] = error_code
    return payload


async def _find_processing_device(
    api: APITestHelper, publish_id: int, timeout_seconds: float = 2.0
) -> dict[str, Any] | None:
    """Poll publish progress for a device with result_status == PROCESSING."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_seconds:
        devices = await get_devices_from_progress(api, publish_id)
        for d in devices:
            if d.get("device_uuid") and d.get("result_status") == "PROCESSING":
                return d
        await asyncio.sleep(0.05)
    return None


class TestTeclawCallbackHappyPath:
    """Full-stack TeClaw callback success flow (router-level conversion)."""

    @pytest.mark.asyncio
    async def test_teclaw_callback_success_flips_device_to_running(
        self,
        api: APITestHelper,
        bootstrap_init: "ApplicationContainer",
        unique_id: str,
    ) -> None:
        """Success callback transitions device PENDING -> ACTIVE and
        publish_record PROCESSING -> SUCCESS via the router-level conversion
        to DeviceCallbackRequest and handle_device_callback."""
        bot = await create_hook_bot(api, f"teclaw-happy-{unique_id}")
        publish_id = bot["publish_id"]
        try:
            code = await approve_publish(api, publish_id)
            assert code == 200, f"Approve failed: {code}"

            device = await _find_processing_device(api, publish_id)
            assert device is not None, (
                f"No PROCESSING device found for publish_id={publish_id}"
            )
            device_uuid = device["device_uuid"]

            # Phase 1 assertion: after async submit, before callback.
            # publish_record stays PROCESSING and device stays PENDING
            # awaiting the external callback.
            pre_device = _get_device(bootstrap_init, device_uuid)
            assert pre_device is not None, "Device not found pre-callback"
            assert pre_device.status == "PENDING", (
                f"Device should be PENDING pre-callback (async submit done, "
                f"awaiting result); got: {pre_device.status}"
            )
            assert device.get("result_status") == "PROCESSING", (
                f"publish_record should be PROCESSING pre-callback; "
                f"got: {device.get('result_status')}"
            )

            payload = _callback_payload(
                device_uuid, publish_id, api.tenant, success=True,
            )
            status_code, body = await call_teclaw_callback(api.client, payload)

            assert status_code == 200, f"Expected 200, got {status_code}: {body}"
            assert body["code"] == 0, f"Expected code==0, got: {body}"
            assert body["data"]["status"] == "processed", (
                f"Expected data.status=='processed', got: {body}"
            )

            # Phase 2 assertion: after success callback.
            # device PENDING -> ACTIVE, publish_record PROCESSING -> SUCCESS.
            db_device = _get_device(bootstrap_init, device_uuid)
            assert db_device is not None, "Device not found after callback"
            assert db_device.status == "ACTIVE", (
                f"Device should be ACTIVE after success callback; "
                f"got status={db_device.status}"
            )

            devices = await get_devices_from_progress(api, publish_id)
            target = next(
                (d for d in devices if d.get("device_uuid") == device_uuid),
                None,
            )
            assert target is not None, (
                "Device not found in progress after callback"
            )
            assert target.get("result_status") == "SUCCESS", (
                f"publish_record result_status should be 'SUCCESS'; "
                f"got: {target.get('result_status')}"
            )
        finally:
            await cleanup_bot(api, bot["bot_uuid"])