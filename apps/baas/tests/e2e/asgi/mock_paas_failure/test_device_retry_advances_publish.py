"""AGI/E2E: a recovered device must drive its publish forward.

End-to-end contract for the operator-authorized device retry
(``POST /api/v1/publishes/{publish_id}/devices/{device_uuid}/retry``):

    device fails -> publish FAILED
                 -> retry device -> device reports start-hook success
                 -> record SUCCESS -> batch COMPLETED
                 -> publish leaves FAILED and continues advancing

Everything here runs through the real ASGI app and the real SQLite-backed BaaS
services; only the PaaS provider is mocked, which is what lets one device fail
deterministically while the rest succeed.

Why the callback is driven explicitly: the mock PaaS answers ``COMPLETED`` for
device creation, so BaaS takes its *inline* success path. A real ARCA device
answers ``HOOK_PENDING`` and reports success later through
``/api/v1/publish/device-callback``. Driving the callback here is what
exercises the inbound path that must reconcile the publish.
"""

from __future__ import annotations

import uuid

import pytest

from secbaas.community.core.service.paas._mock_paas_service import (
    reset_create_failure_counter,
)
from tests.e2e.asgi.conftest import (
    ASYNC_POLL_TIMEOUT,
    APITestHelper,
    approve_publish,
    call_device_callback,
    create_test_bot,
    get_devices_from_progress,
    wait_for_publish_status,
)

pytestmark = [pytest.mark.mock_paas_create_failure]


def _fail_first_creates(monkeypatch: pytest.MonkeyPatch, times: int) -> None:
    """Make the first N device creations fail, then succeed."""
    monkeypatch.setenv("PAAS_MOCK_MODE", "true")
    monkeypatch.delenv("PAAS_MOCK_CREATE_FAILURE", raising=False)
    monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", str(times))
    reset_create_failure_counter()


def _clear_create_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop failing creations for the rest of the test.

    Removing the env var is what actually disables the mock's failure mode:
    clearing the elapsed counter alone leaves the budget set, so the next
    re-provision would start a fresh round of failures.
    """
    monkeypatch.delenv("PAAS_MOCK_CREATE_FAIL_TIMES", raising=False)
    monkeypatch.delenv("PAAS_MOCK_CREATE_FAILURE", raising=False)
    reset_create_failure_counter()


async def _progress(api: APITestHelper, publish_id: int) -> dict:
    resp = await api.client.get(
        api.publish_url(publish_id, "progress"),
        params=api.params(include_devices="true"),
    )
    assert resp.status_code == 200
    return resp.json()["data"]


async def _publish_status(api: APITestHelper, publish_id: int) -> str:
    resp = await api.client.get(api.publish_url(publish_id), params=api.params())
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["status"]


def _failed_device(devices: list[dict]) -> dict:
    failed = [d for d in devices if d.get("result_status") == "FAILED"]
    assert failed, f"expected a failed device, got {devices}"
    return failed[0]


class TestDeviceRetryDrivesPublish:
    """One device fails; recovering it must move the publish forward."""

    @pytest.mark.asyncio
    async def test_retried_device_success_reopens_failed_publish(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        # Auto-retry disabled: the manual budget is the only way back, which is
        # exactly the production default this feature exists for.
        _fail_first_creates(monkeypatch, times=1)
        bot = await create_test_bot(
            api,
            f"device-retry-agi-{unique_id}",
            publish_max_retry_times=0,
        )
        publish_id = bot["publish_id"]

        assert await approve_publish(api, publish_id) == 200

        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED", f"expected FAILED before retry, got {status}"

        devices = await get_devices_from_progress(api, publish_id)
        failed = _failed_device(devices)
        device_uuid = failed["device_uuid"]
        assert device_uuid, "failed device must carry its uuid"

        # Stop failing creations: from here on every re-provision must succeed.
        _clear_create_failures(monkeypatch)

        # ── Operator-authorized retry of that single device ────────────────
        retry = await api.client.post(
            f"/api/v1/publishes/{publish_id}/devices/{device_uuid}/retry",
            params=api.params(),
            json={"operator": "agi-test", "request_id": uuid.uuid4().hex},
        )
        assert retry.status_code == 200, retry.text
        payload = retry.json()["data"]
        assert payload["result_status"] == "PROCESSING"
        assert payload["publish_manual_retry_count"] == 1

        # The device is re-provisioned and settles immediately (inline path),
        # after which the orchestrator reconciles the publish on its own.
        driven = await wait_for_publish_status(
            api,
            publish_id,
            {"SUCCESS", "ACTIVE", "APPROVING", "FAILED"},
            timeout_seconds=ASYNC_POLL_TIMEOUT,
        )

        # ── Device actually came back ─────────────────────────────────────
        devices_after = await get_devices_from_progress(api, publish_id)
        recovered = [d for d in devices_after if d.get("device_uuid") == device_uuid]
        assert recovered, "retried device must still be listed in the publish"
        assert recovered[0]["result_status"] == "SUCCESS", recovered[0]
        assert recovered[0]["publish_manual_retry_count"] == 1

        # ── And it drove the publish off FAILED ───────────────────────────
        assert driven != "FAILED", (
            "publish must leave FAILED once its only failing device recovered; "
            f"got {driven}"
        )
        final = await _publish_status(api, publish_id)
        assert final != "FAILED", f"final publish status must progress, got {final}"

    @pytest.mark.asyncio
    async def test_start_hook_callback_reopens_failed_publish(
        self,
        api: APITestHelper,
        monkeypatch: pytest.MonkeyPatch,
        unique_id: str,
        bootstrap_init,
    ) -> None:
        """The production ARCA shape: success arrives via the start callback.

        A real ARCA sandbox answers ``HOOK_PENDING`` and reports success later
        through the device-callback route. The mock answers ``COMPLETED``
        inline, so force the pending outcome — otherwise this test would pass
        even if the callback path never reconciled.
        """
        from secbaas.community.api.device_manage import DeviceOperationOutcome

        _fail_first_creates(monkeypatch, times=1)
        bot = await create_test_bot(
            api,
            f"device-retry-callback-{unique_id}",
            publish_max_retry_times=0,
        )
        publish_id = bot["publish_id"]

        assert await approve_publish(api, publish_id) == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED"

        devices = await get_devices_from_progress(api, publish_id)
        device_uuid = _failed_device(devices)["device_uuid"]

        # Make every subsequent re-provision succeed, independent of how much
        # of the global failure budget the initial publish consumed.
        _clear_create_failures(monkeypatch)

        # Force the retried provision to leave the record PROCESSING, the way a
        # real sandbox does when it has not yet called back.
        publish_service = bootstrap_init.services.publish_service()
        real_provision = publish_service._provision_record_attempt

        async def _pending_provision(**kwargs):
            await real_provision(**kwargs)
            return DeviceOperationOutcome.HOOK_PENDING

        monkeypatch.setattr(
            publish_service, "_provision_record_attempt", _pending_provision
        )

        retry = await api.client.post(
            f"/api/v1/publishes/{publish_id}/devices/{device_uuid}/retry",
            params=api.params(),
            json={"operator": "agi-test", "request_id": uuid.uuid4().hex},
        )
        assert retry.status_code == 200, retry.text

        # The device has not reported yet: the publish must still be FAILED.
        devices_pending = await get_devices_from_progress(api, publish_id)
        pending = [d for d in devices_pending if d.get("device_uuid") == device_uuid]
        assert pending and pending[0]["result_status"] == "PROCESSING", pending
        assert await _publish_status(api, publish_id) == "FAILED"

        # Replay the device's own start-hook success through the public
        # callback route — the path a real sandbox takes.
        callback = await call_device_callback(
            api,
            device_uuid=device_uuid,
            publish_id=publish_id,
            result_status="SUCCESS",
            exit_code=0,
            stdout="start_service.sh ok",
        )
        assert callback.status_code == 200, callback.text
        assert callback.json()["data"]["status"] == "processed", callback.text

        status_after = await wait_for_publish_status(
            api,
            publish_id,
            {"SUCCESS", "ACTIVE", "APPROVING"},
            timeout_seconds=ASYNC_POLL_TIMEOUT,
        )
        assert status_after != "FAILED", (
            "start-hook callback for a recovered device must reopen the "
            f"publish; got {status_after}"
        )

    @pytest.mark.asyncio
    async def test_recovering_every_failed_device_reopens_multi_batch_publish(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        """The publish 105597 shape: one batch fails, later batches never run.

        With every creation failing, batch 1 reports FAILED while batches 2..n
        stay PENDING and their records are flipped to FAILED by stage cleanup.
        Recovering batch 1's devices cannot complete the stage through ordinary
        batch completion — the later batches never ran — so only the reconcile
        can decide the stage is recoverable. This is the case the feature exists
        for.
        """
        _fail_first_creates(monkeypatch, times=99)
        bot = await create_test_bot(
            api,
            f"device-retry-multi-{unique_id}",
            device_count=6,
            publish_max_retry_times=0,
        )
        publish_id = bot["publish_id"]

        assert await approve_publish(api, publish_id) == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED", f"expected FAILED first, got {status}"

        # From here on, re-provisioning must succeed.
        _clear_create_failures(monkeypatch)

        # Only the genuinely-executed batch carries real failures; the rest are
        # records that never ran.
        failed = [
            d
            for d in await get_devices_from_progress(api, publish_id)
            if d.get("result_status") == "FAILED"
        ]
        assert failed, "expected failed devices"

        accepted = 0
        for device in failed:
            resp = await api.client.post(
                f"/api/v1/publishes/{publish_id}/devices/{device['device_uuid']}/retry",
                params=api.params(),
                json={"operator": "agi-test", "request_id": uuid.uuid4().hex},
            )
            if resp.status_code == 200:
                accepted += 1
                continue
            # A reopened-and-completed publish correctly refuses more retries.
            assert resp.json()["detail"]["error_code"] == "PUBLISH_TERMINAL", resp.text
            break

        assert accepted >= 1, "expected at least one retry to be accepted"

        final = await wait_for_publish_status(
            api,
            publish_id,
            {"SUCCESS", "ACTIVE", "APPROVING"},
            timeout_seconds=ASYNC_POLL_TIMEOUT,
        )
        assert final != "FAILED", (
            f"recovering the failed devices must reopen the publish; got {final}"
        )

        final = await wait_for_publish_status(
            api,
            publish_id,
            {"SUCCESS", "ACTIVE", "APPROVING"},
            timeout_seconds=ASYNC_POLL_TIMEOUT,
        )
        assert final != "FAILED", (
            f"recovering the failed devices must reopen the publish; got {final}"
        )

    @pytest.mark.asyncio
    async def test_retry_rejected_when_publish_already_succeeded(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        """Terminal publishes cannot be retried into."""
        _fail_first_creates(monkeypatch, times=0)
        bot = await create_test_bot(api, f"device-retry-terminal-{unique_id}")
        publish_id = bot["publish_id"]

        assert await approve_publish(api, publish_id) == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "SUCCESS"

        devices = await get_devices_from_progress(api, publish_id)
        assert devices
        device_uuid = devices[0]["device_uuid"]

        resp = await api.client.post(
            f"/api/v1/publishes/{publish_id}/devices/{device_uuid}/retry",
            params=api.params(),
            json={"operator": "agi-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code in (404, 409), resp.text
        assert resp.json()["detail"]["error_code"] in (
            "PUBLISH_TERMINAL",
            "RECORD_NOT_FAILED",
            "DEVICE_NOT_IN_PUBLISH",
        )


class TestBulkRetryFailedDevices:
    """Retry every failed device of a publish in one call."""

    @pytest.mark.asyncio
    async def test_bulk_retry_recovers_publish(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _fail_first_creates(monkeypatch, times=99)
        bot = await create_test_bot(
            api,
            f"bulk-retry-{unique_id}",
            device_count=6,
            publish_max_retry_times=0,
        )
        publish_id = bot["publish_id"]

        assert await approve_publish(api, publish_id) == 200
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED"}, timeout_seconds=ASYNC_POLL_TIMEOUT
        )
        assert status == "FAILED"

        failed_before = [
            d
            for d in await get_devices_from_progress(api, publish_id)
            if d.get("result_status") == "FAILED"
        ]
        assert failed_before, "expected failed devices to retry"

        # From here on, re-provisioning must succeed.
        _clear_create_failures(monkeypatch)

        resp = await api.client.post(
            f"/api/v1/publishes/{publish_id}/devices/retry-failed",
            params=api.params(),
            json={"operator": "agi-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["requested"] >= 1
        assert data["accepted_count"] >= 1
        assert data["accepted_count"] + data["skipped_count"] == data["requested"]
        for entry in data["accepted"]:
            assert entry["device_uuid"]
            assert entry["publish_manual_retry_count"] == 1

        final = await wait_for_publish_status(
            api,
            publish_id,
            {"SUCCESS", "ACTIVE", "APPROVING"},
            timeout_seconds=ASYNC_POLL_TIMEOUT,
        )
        assert final != "FAILED", (
            f"bulk retry must move the publish forward; got {final}"
        )

    @pytest.mark.asyncio
    async def test_bulk_retry_rejected_on_terminal_publish(
        self, api: APITestHelper, monkeypatch: pytest.MonkeyPatch, unique_id: str
    ) -> None:
        _fail_first_creates(monkeypatch, times=0)
        bot = await create_test_bot(api, f"bulk-terminal-{unique_id}")
        publish_id = bot["publish_id"]

        assert await approve_publish(api, publish_id) == 200
        assert (
            await wait_for_publish_status(
                api, publish_id, {"SUCCESS"}, timeout_seconds=ASYNC_POLL_TIMEOUT
            )
            == "SUCCESS"
        )

        resp = await api.client.post(
            f"/api/v1/publishes/{publish_id}/devices/retry-failed",
            params=api.params(),
            json={"operator": "agi-test", "request_id": uuid.uuid4().hex},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error_code"] == "PUBLISH_TERMINAL"
