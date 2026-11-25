"""Service-level tests for device retry preconditions and reconcile plumbing.

The router tests prove the error codes map to HTTP statuses; these prove the
service raises them for the right conditions, and exercise the reconcile
helpers that the callback/inline paths only reach indirectly.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.publish_manage import PublishStatus
from secbaas.community.api.template_manage import ArcaTemplateConfig
from secbaas.community.core.repository.publish_record import (
    PublishRecordExtraConfig,
)
from secbaas.community.core.service.publish_manage import DefaultPublishService
from secbaas.community.core.service.publish_manage._publish_retry_orchestrator import (
    STAGE_CLEANUP_MESSAGE,
)


def _svc() -> DefaultPublishService:
    svc = DefaultPublishService(
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
    svc._publish_record_repo.database_now.return_value = datetime.now()
    svc._device_repo.get_by_device_uuid.return_value = MagicMock(
        provider_type="ARCA", id=11
    )
    svc._template_service.get_default_or_explicit_template.return_value = MagicMock(
        config=ArcaTemplateConfig.model_construct()
    )
    return svc


def _publish(status: str = "FAILED", extra_config: dict | None = None) -> MagicMock:
    pub = MagicMock()
    pub.id = 9
    pub.publish_type = "UPDATE"
    pub.status = status
    pub.gmt_create = datetime.now() - timedelta(minutes=5)
    pub.extra_config = extra_config or {}
    return pub


def _record(
    *, id: int = 1, status: str = "FAILED", result_message: str = "prov boom"
) -> MagicMock:
    rec = MagicMock()
    rec.id = id
    rec.device_uuid = "dev-1"
    rec.result_status = status
    rec.batch_id = 5
    rec.publish_id = 9
    rec.result_message = result_message
    rec.extra_config = {}
    return rec


@pytest.fixture
def env():
    with (
        patch(
            "secbaas.community.core.service.publish_manage"
            "._publish_retry_orchestrator.get_current_env",
            return_value="test",
        ),
        patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ),
    ):
        yield


class TestRetryDevicePreconditions:
    async def test_missing_publish(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="PUBLISH_NOT_FOUND"):
            await svc.retry_device(
                tenant="t", publish_id=9, device_uuid="dev-1", operator="op"
            )

    @pytest.mark.parametrize("status", ["SUCCESS", "REVOKED", "REJECTED"])
    async def test_terminal_publish(self, env, status):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish(status=status)
        with pytest.raises(ValueError, match="PUBLISH_TERMINAL"):
            await svc.retry_device(
                tenant="t", publish_id=9, device_uuid="dev-1", operator="op"
            )

    async def test_unknown_device(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._device_repo.get_by_device_uuid.return_value = None
        with pytest.raises(ValueError, match="DEVICE_NOT_IN_PUBLISH"):
            await svc.retry_device(
                tenant="t", publish_id=9, device_uuid="dev-1", operator="op"
            )

    async def test_device_without_record_in_publish(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.get_by_device_id_and_publish_id.return_value = None
        with pytest.raises(ValueError, match="DEVICE_NOT_IN_PUBLISH"):
            await svc.retry_device(
                tenant="t", publish_id=9, device_uuid="dev-1", operator="op"
            )

    async def test_record_not_failed(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.get_by_device_id_and_publish_id.return_value = _record(
            status="SUCCESS"
        )
        with pytest.raises(ValueError, match="RECORD_NOT_FAILED"):
            await svc.retry_device(
                tenant="t", publish_id=9, device_uuid="dev-1", operator="op"
            )

    async def test_provider_unsupported(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.get_by_device_id_and_publish_id.return_value = (
            _record()
        )
        svc._publish_record_repo.get_retry_state.return_value = (
            PublishRecordExtraConfig(device_uuid="dev-1")
        )
        svc._template_service.get_default_or_explicit_template.return_value = MagicMock(
            config=MagicMock()
        )
        with pytest.raises(ValueError, match="PROVIDER_RETRY_UNSUPPORTED"):
            await svc.retry_device(
                tenant="t", publish_id=9, device_uuid="dev-1", operator="op"
            )

    async def test_budget_exhausted(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.get_by_device_id_and_publish_id.return_value = (
            _record()
        )
        svc._publish_record_repo.get_retry_state.return_value = (
            PublishRecordExtraConfig(device_uuid="dev-1", publish_manual_retry_count=3)
        )
        with pytest.raises(ValueError, match="MANUAL_RETRY_EXHAUSTED"):
            await svc.retry_device(
                tenant="t",
                publish_id=9,
                device_uuid="dev-1",
                operator="op",
                max_manual_retries=3,
            )

    async def test_invalid_budget_falls_back_to_default(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.get_by_device_id_and_publish_id.return_value = (
            _record()
        )
        svc._publish_record_repo.get_retry_state.return_value = (
            PublishRecordExtraConfig(device_uuid="dev-1")
        )
        svc._drive_record_attempt = AsyncMock(return_value=True)
        await svc.retry_device(
            tenant="t",
            publish_id=9,
            device_uuid="dev-1",
            operator="op",
            max_manual_retries=0,
        )
        assert svc._drive_record_attempt.call_args.kwargs["max_manual_retries"] >= 1

    async def test_claim_lost_reports_exhausted(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.get_by_device_id_and_publish_id.return_value = (
            _record()
        )
        svc._publish_record_repo.get_retry_state.return_value = (
            PublishRecordExtraConfig(device_uuid="dev-1")
        )
        svc._drive_record_attempt = AsyncMock(return_value=False)
        with pytest.raises(ValueError, match="MANUAL_RETRY_EXHAUSTED"):
            await svc.retry_device(
                tenant="t", publish_id=9, device_uuid="dev-1", operator="op"
            )


class TestReconcileHelpers:
    def test_failed_from_status_recorded(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish(
            status="ACTIVE", extra_config={}
        )
        svc._record_failed_from_status(tenant="t", publish_id=9)
        payload = svc._publish_repo.update_publish.call_args.kwargs["extra_config"]
        assert payload["failed_from_status"] == "ACTIVE"

    def test_failed_from_status_is_not_overwritten(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = _publish(
            status="FAILED", extra_config={"failed_from_status": "ACTIVE"}
        )
        svc._record_failed_from_status(tenant="t", publish_id=9)
        svc._publish_repo.update_publish.assert_not_called()

    def test_failed_from_status_missing_publish_is_noop(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = None
        svc._record_failed_from_status(tenant="t", publish_id=9)
        svc._publish_repo.update_publish.assert_not_called()

    def test_mark_cleanup_record_tags_extra_config(self, env):
        svc = _svc()
        rec = _record(result_message=STAGE_CLEANUP_MESSAGE)
        svc._mark_stage_cleanup_record(rec, tenant="t")
        payload = svc._publish_record_repo.update_extra_config.call_args.kwargs[
            "extra_config"
        ]
        assert payload["cleanup_reason"] == "stage_failed"

    def test_mark_cleanup_record_without_id_is_noop(self, env):
        svc = _svc()
        svc._mark_stage_cleanup_record(MagicMock(id=None), tenant="t")
        svc._publish_record_repo.update_extra_config.assert_not_called()


class TestStageFailuresRecovered:
    def _svc(self, *, batches, records):
        svc = _svc()
        svc._publish_batch_repo.list_by_publish_id.return_value = batches
        svc._publish_record_repo.list_by_publish_id.return_value = records
        return svc

    def test_no_batches_is_not_recovered(self, env):
        svc = self._svc(batches=[], records=[])
        assert svc._stage_failures_recovered(tenant="t", publish_id=9) is False

    def test_pending_batch_blocks(self, env):
        svc = self._svc(
            batches=[MagicMock(status="PENDING")],
            records=[_record(status="SUCCESS")],
        )
        assert svc._stage_failures_recovered(tenant="t", publish_id=9) is False

    def test_cleanup_only_failures_count_as_recovered(self, env):
        svc = self._svc(
            batches=[MagicMock(status="COMPLETED")],
            records=[_record(status="FAILED", result_message=STAGE_CLEANUP_MESSAGE)],
        )
        assert svc._stage_failures_recovered(tenant="t", publish_id=9) is True

    def test_real_failure_blocks(self, env):
        svc = self._svc(
            batches=[MagicMock(status="COMPLETED")],
            records=[_record(status="FAILED", result_message="prov boom")],
        )
        assert svc._stage_failures_recovered(tenant="t", publish_id=9) is False


class TestResolveReconcileTarget:
    @pytest.mark.parametrize(
        "config, expected",
        [
            ({"failed_from_status": "ACTIVE"}, "ACTIVE"),
            ({"failed_from_status": "APPROVING"}, "APPROVING"),
            ({}, PublishStatus.ACTIVE.value),
            ({"failed_from_status": "SUCCESS"}, PublishStatus.ACTIVE.value),
            ({"failed_from_status": "FAILED"}, PublishStatus.ACTIVE.value),
        ],
    )
    def test_target(self, config, expected):
        assert DefaultPublishService._resolve_reconcile_target(config) == expected


class TestReconcileExceptionPath:
    async def test_exception_is_logged_not_raised(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.side_effect = RuntimeError("boom")
        # Must not propagate: reconcile is best-effort.
        await svc._reconcile_failed_stage(tenant="t", publish_id=9, operator="op")


class TestAdvanceAfterStage:
    async def test_missing_publish_is_noop(self, env):
        svc = _svc()
        svc._publish_repo.get_by_id.return_value = None
        await svc._advance_after_stage(tenant="t", publish_id=9, modifier="op")
        svc._publish_repo.update_status.assert_not_called()


class TestStageCleanupMarking:
    async def test_stage_failure_marks_cleanup_records(self, env):
        """The durable marker must be written for records that never ran."""
        svc = _svc()
        publish = _publish(status="ACTIVE", extra_config={})
        svc._publish_repo.get_by_id.return_value = publish
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(id=5, stage="PREPUB")
        ]
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
            _record(id=2, status="PENDING")
        ]

        await svc._check_stage_advancement(
            tenant="t", publish_id=9, current_stage="PREPUB", stage_failed=True
        )

        svc._publish_repo.update_status.assert_called()
        kwargs = svc._publish_record_repo.update_extra_config.call_args.kwargs
        assert kwargs["extra_config"]["cleanup_reason"] == "stage_failed"
