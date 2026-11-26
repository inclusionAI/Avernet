"""Unit tests for operator-authorized device retry and stage reconcile.

Covers the two behaviours the manual retry endpoint depends on:

* a FAILED record can be retried even when the publish never enabled the
  automatic retry budget (``publish_max_retry_times = 0``, the default);
* a publish marked FAILED by a stage failure is reopened once the real
  failures are gone, while records that merely inherited their stage's failure
  do not count as real failures.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from secbaas.community.api.device_manage import DeviceOperationOutcome
from secbaas.community.api.publish_manage import PublishStatus
from secbaas.community.api.template_manage import ArcaTemplateConfig
from secbaas.community.core.repository.publish_record import (
    PublishRecordExtraConfig,
)
from secbaas.community.core.service.publish_manage import DefaultPublishService
from secbaas.community.core.service.publish_manage._publish_retry_orchestrator import (
    STAGE_CLEANUP_MESSAGE,
    is_cleanup_record,
)


def _build_service() -> DefaultPublishService:
    return DefaultPublishService(
        bot_repo=MagicMock(),
        device_repo=MagicMock(),
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=MagicMock(),
        batch_repo=MagicMock(),
        publish_record_repo=MagicMock(),
        template_service=MagicMock(),
        bot_service=MagicMock(),
        device_service=MagicMock(),
    )


def _make_service() -> DefaultPublishService:
    svc = _build_service()
    svc._publish_record_repo.database_now.return_value = datetime.now()
    svc._device_repo.get_by_device_uuid.return_value = MagicMock(
        provider_type="ARCA", id=11
    )
    svc._template_service.get_default_or_explicit_template.return_value = MagicMock(
        config=ArcaTemplateConfig.model_construct()
    )
    return svc


def _make_record(
    *,
    id: int = 1,
    device_uuid: str = "dev-1",
    result_status: str = "FAILED",
    batch_id: int | None = 5,
    publish_id: int = 9,
    result_message: str = "prov boom",
) -> MagicMock:
    rec = MagicMock()
    rec.id = id
    rec.device_uuid = device_uuid
    rec.result_status = result_status
    rec.batch_id = batch_id
    rec.publish_id = publish_id
    rec.result_message = result_message
    rec.extra_config = {}
    return rec


def _state(
    *,
    publish_max_retry_times: int = 0,
    publish_retry_count: int = 0,
    publish_manual_retry_count: int = 0,
) -> PublishRecordExtraConfig:
    return PublishRecordExtraConfig(
        device_uuid="dev-1",
        provider_device_id="prov-1",
        publish_max_retry_times=publish_max_retry_times,
        publish_retry_count=publish_retry_count,
        publish_manual_retry_count=publish_manual_retry_count,
    )


def _publish(status: str = "FAILED", extra_config: dict | None = None) -> MagicMock:
    pub = MagicMock()
    pub.id = 9
    pub.publish_type = "UPDATE"
    pub.status = status
    pub.gmt_create = datetime.now() - timedelta(minutes=5)
    pub.extra_config = extra_config or {}
    return pub


def _patch_env():
    return patch(
        "secbaas.community.core.service.publish_manage"
        "._publish_retry_orchestrator.get_current_env",
        return_value="test",
    )


class TestManualRetryBudget:
    async def test_manual_retry_allowed_with_zero_auto_budget(self):
        """The whole point: auto-retry disabled must not block a manual retry."""
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=0
        )
        svc._publish_record_repo.try_claim_manual_retry.return_value = True
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._device_service.destroy_device_by_uuid = AsyncMock()
        svc._device_service.prepare_for_reprovision = MagicMock(return_value=True)
        svc._device_service.update_device = AsyncMock(
            return_value=MagicMock(
                operation_outcome=DeviceOperationOutcome.HOOK_PENDING
            )
        )
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="manual",
                manual=True,
            )
        assert started is True
        svc._publish_record_repo.try_claim_manual_retry.assert_called_once()

    async def test_manual_retry_exhausted(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_manual_retry_count=3
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="manual",
                manual=True,
                max_manual_retries=3,
            )
        assert started is False
        svc._publish_record_repo.try_claim_manual_retry.assert_not_called()

    async def test_manual_retry_requires_failed_record(self):
        svc = _make_service()
        rec = _make_record(result_status="PROCESSING")
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="manual",
                manual=True,
            )
        assert started is False
        svc._publish_record_repo.try_claim_manual_retry.assert_not_called()

    async def test_lost_claim_does_not_provision(self):
        """Two concurrent callers: the loser must not touch the provider."""
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_record_repo.try_claim_manual_retry.return_value = False
        svc._publish_repo.get_by_id.return_value = _publish()
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="manual",
                manual=True,
            )
        assert started is False
        svc._device_service.start_device.assert_not_called()
        svc._device_service.update_device.assert_not_called()


class TestCleanupRecordMarker:
    def test_marker_from_extra_config(self):
        rec = _make_record(
            result_message="something else",
        )
        rec.extra_config = {"cleanup_reason": "stage_failed"}
        assert is_cleanup_record(rec) is True

    def test_legacy_message_fallback(self):
        rec = _make_record(result_message=STAGE_CLEANUP_MESSAGE)
        rec.extra_config = {}
        assert is_cleanup_record(rec) is True

    def test_real_failure_is_not_cleanup(self):
        rec = _make_record(result_message="Device update failed: boom")
        rec.extra_config = {}
        assert is_cleanup_record(rec) is False


class TestReconcileFailedStage:
    async def _run(self, svc, *, batches, records, publish=None, cas_result=True):
        svc._publish_repo.get_by_id.return_value = publish or _publish(
            extra_config={"failed_from_status": "ACTIVE"}
        )
        svc._publish_batch_repo.list_by_publish_id.return_value = batches
        svc._publish_record_repo.list_by_publish_id.return_value = records
        svc._publish_repo.update_status.return_value = cas_result
        svc._advance_after_stage = AsyncMock()
        with _patch_env():
            await svc._reconcile_failed_stage(tenant="t", publish_id=9, operator="op")

    async def test_reconciles_when_only_cleanup_failures_remain(self):
        svc = _make_service()
        batch = MagicMock(status="COMPLETED")
        ok = _make_record(id=1, result_status="SUCCESS")
        cleanup = _make_record(
            id=2, result_status="FAILED", result_message=STAGE_CLEANUP_MESSAGE
        )
        await self._run(svc, batches=[batch], records=[ok, cleanup])
        svc._publish_repo.update_status.assert_called_once()
        assert svc._publish_repo.update_status.call_args.kwargs["status"] == "ACTIVE"
        assert (
            svc._publish_repo.update_status.call_args.kwargs["expected_status"]
            == PublishStatus.FAILED.value
        )
        svc._advance_after_stage.assert_awaited_once()

    async def test_keeps_failed_when_real_failure_remains(self):
        svc = _make_service()
        batch = MagicMock(status="COMPLETED")
        real = _make_record(id=2, result_status="FAILED", result_message="prov boom")
        await self._run(svc, batches=[batch], records=[real])
        svc._publish_repo.update_status.assert_not_called()
        svc._advance_after_stage.assert_not_awaited()

    async def test_keeps_failed_when_batch_still_failed(self):
        svc = _make_service()
        batch = MagicMock(status="FAILED")
        ok = _make_record(id=1, result_status="SUCCESS")
        await self._run(svc, batches=[batch], records=[ok])
        svc._publish_repo.update_status.assert_not_called()

    async def test_noop_when_publish_not_failed(self):
        svc = _make_service()
        await self._run(
            svc,
            batches=[MagicMock(status="COMPLETED")],
            records=[],
            publish=_publish(status="ACTIVE"),
        )
        svc._publish_repo.update_status.assert_not_called()

    async def test_restore_target_falls_back_to_active(self):
        svc = _make_service()
        await self._run(
            svc,
            batches=[MagicMock(status="COMPLETED")],
            records=[_make_record(id=1, result_status="SUCCESS")],
            publish=_publish(status="FAILED", extra_config={}),
        )
        assert svc._publish_repo.update_status.call_args.kwargs["status"] == "ACTIVE"

    async def test_lost_cas_does_not_advance(self):
        svc = _make_service()
        await self._run(
            svc,
            batches=[MagicMock(status="COMPLETED")],
            records=[_make_record(id=1, result_status="SUCCESS")],
            cas_result=False,
        )
        svc._publish_repo.update_status.assert_called_once()
        svc._advance_after_stage.assert_not_awaited()


class TestCallbackPathTriggersReconcile:
    """A retried device usually reports success via the device-callback route.

    The orchestrator's inline success path reconciles, but the inbound callback
    path historically only advanced the batch. Both must reconcile, otherwise a
    retried device can succeed while its publish stays FAILED.
    """

    async def test_device_callback_reconciles_after_success(self):
        svc = _make_service()
        svc._check_batch_completion = AsyncMock()
        svc._reconcile_failed_stage = AsyncMock()
        svc._publish_record_repo.get_processing_record_by_device_and_publish.return_value = _make_record(
            id=1, result_status="PROCESSING", batch_id=5
        )
        svc._publish_record_repo.update_result_if_processing.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(id=11)

        callback = MagicMock()
        callback.event_type = "start"
        callback.publish_id = 9
        callback.result_status = "SUCCESS"
        callback.device_uuid = "dev-1"
        callback.tenant = "t"
        callback.exit_code = 0
        callback.stdout = ""
        callback.stderr = None

        with _patch_env():
            await svc.handle_device_callback(callback)

        svc._check_batch_completion.assert_awaited()
        svc._reconcile_failed_stage.assert_awaited_once()
