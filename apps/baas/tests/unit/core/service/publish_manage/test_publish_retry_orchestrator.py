"""Unit tests for the per-device publish attempt orchestrator."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_manage import BotConfig
from secbaas.community.api.device_manage import DeviceOperationOutcome
from secbaas.community.api.publish_manage import PublishConfig
from secbaas.community.api.template_manage import (
    ArcaTemplateConfig,
    DockerTemplateConfig,
    K8sTemplateConfig,
    LocalTemplateConfig,
    PoolabTemplateConfig,
    SigmaTemplateConfig,
    TeClawTemplateConfig,
)
from secbaas.community.core.repository.publish_record import (
    PublishRecordExtraConfig,
)
from secbaas.community.core.service.publish_manage import DefaultPublishService
from secbaas.community.core.utils.provision_retry import attempt_started_at


def _make_service() -> DefaultPublishService:
    svc = _build_service()
    svc._publish_record_repo.database_now.return_value = datetime.now()
    svc._device_repo.get_by_device_uuid.return_value = MagicMock(provider_type="ARCA")
    svc._template_service.get_default_or_explicit_template.return_value = MagicMock(
        config=ArcaTemplateConfig.model_construct()
    )
    return svc


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


def _make_record(
    *,
    id: int = 1,
    device_uuid: str = "dev-1",
    result_status: str = "PROCESSING",
    batch_id: int | None = 5,
    publish_id: int = 9,
    extra_config: dict | None = None,
) -> MagicMock:
    rec = MagicMock()
    rec.id = id
    rec.device_uuid = device_uuid
    rec.result_status = result_status
    rec.batch_id = batch_id
    rec.publish_id = publish_id
    rec.extra_config = extra_config or {}
    return rec


def _state(
    *,
    publish_max_retry_times: int = 2,
    publish_retry_count: int = 0,
) -> PublishRecordExtraConfig:
    return PublishRecordExtraConfig(
        device_uuid="dev-1",
        provider_device_id="prov-1",
        publish_max_retry_times=publish_max_retry_times,
        publish_retry_count=publish_retry_count,
    )


def _publish(created_offset_seconds: int = 0) -> MagicMock:
    pub = MagicMock()
    pub.id = 9
    pub.publish_type = "CREATE"
    pub.gmt_create = datetime.now() + timedelta(seconds=created_offset_seconds)
    return pub


def _patch_env():
    return patch(
        "secbaas.community.core.service.publish_manage"
        "._publish_retry_orchestrator.get_current_env",
        return_value="test",
    )


class TestDriveRecordAttemptGuards:
    async def test_terminal_record_is_not_retried(self):
        svc = _make_service()
        rec = _make_record(result_status="FAILED")
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        svc._publish_record_repo.try_claim_retry.assert_not_called()

    async def test_zero_budget_never_claims(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=0
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        svc._publish_record_repo.try_claim_retry.assert_not_called()

    async def test_exhausted_budget_never_claims(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=2, publish_retry_count=2
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        svc._publish_record_repo.try_claim_retry.assert_not_called()

    async def test_publish_deadline_stops_retry(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish(
            created_offset_seconds=-7200
        )
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        svc._publish_record_repo.try_claim_retry.assert_not_called()

    async def test_lost_claim_is_noop(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = False
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        svc._device_service.prepare_for_reprovision.assert_not_called()
        svc._device_service.start_device.assert_not_called()


class TestDriveRecordAttemptOrdering:
    async def _run(self, svc, rec, outcome=DeviceOperationOutcome.HOOK_PENDING):
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id="prov-1"
        )
        svc._device_service.destroy_device_by_uuid = AsyncMock(return_value=None)
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
            return_value=MagicMock(operation_outcome=outcome)
        )
        with _patch_env():
            return await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )

    async def test_claim_precedes_destroy_and_create(self):
        svc = _make_service()
        rec = _make_record()
        order: list[str] = []

        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.side_effect = lambda **_: (
            order.append("claim") or True
        )
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id="prov-1"
        )

        async def _destroy(**_):
            order.append("destroy")

        svc._device_service.destroy_device_by_uuid.side_effect = _destroy

        def _prepare(**_):
            order.append("prepare")
            return True

        svc._device_service.prepare_for_reprovision.side_effect = _prepare

        async def _start(**_):
            order.append("create")
            return MagicMock(operation_outcome=DeviceOperationOutcome.HOOK_PENDING)

        svc._device_service.start_device.side_effect = _start

        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )

        assert started is True
        assert order == ["claim", "destroy", "prepare", "create"]

    async def test_failed_destroy_prevents_creation(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id="prov-1"
        )
        svc._device_service.destroy_device_by_uuid = AsyncMock(
            side_effect=RuntimeError("destroy exploded")
        )

        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )

        assert started is False
        svc._device_service.start_device.assert_not_called()
        svc._device_service.prepare_for_reprovision.assert_not_called()

    async def test_missing_sandbox_is_treated_as_destroyed(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id="prov-1"
        )
        svc._device_service.destroy_device_by_uuid = AsyncMock(
            side_effect=RuntimeError("NOT_FOUND")
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
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
                failure_reason="boom",
            )

        assert started is True
        svc._device_service.start_device.assert_awaited_once()

    async def test_device_without_provider_skips_destroy(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
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
                failure_reason="boom",
            )

        assert started is True
        svc._device_service.destroy_device_by_uuid.assert_not_called()


class TestAttemptOutcomes:
    async def _run(self, svc, rec, outcome):
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=2, publish_retry_count=0
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
            return_value=MagicMock(operation_outcome=outcome)
        )
        with _patch_env():
            return await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )

    async def test_hook_pending_keeps_record_in_flight(self):
        svc = _make_service()
        rec = _make_record()
        started = await self._run(svc, rec, DeviceOperationOutcome.HOOK_PENDING)
        assert started is True
        svc._publish_record_repo.update_result.assert_not_called()

    async def test_completed_finalizes_success(self):
        svc = _make_service()
        rec = _make_record()
        svc._check_batch_completion = AsyncMock()
        svc._publish_record_repo.update_result_if_processing.return_value = True
        started = await self._run(svc, rec, DeviceOperationOutcome.COMPLETED)
        assert started is True
        kwargs = svc._publish_record_repo.update_result_if_processing.call_args.kwargs
        assert kwargs["result_status"] == "SUCCESS"
        svc._check_batch_completion.assert_awaited_once()

    async def test_provision_failure_with_budget_remains_retryable(self):
        svc = _make_service()
        rec = _make_record()
        started = await self._run(svc, rec, DeviceOperationOutcome.PROVISION_FAILED)
        assert started is True
        svc._publish_record_repo.update_result.assert_not_called()

    async def test_provision_failure_on_final_attempt_finalizes(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=1, publish_retry_count=0
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
            return_value=MagicMock(
                operation_outcome=DeviceOperationOutcome.PROVISION_FAILED
            )
        )
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        kwargs = svc._publish_record_repo.update_result.call_args.kwargs
        assert kwargs["result_status"] == "FAILED"
        assert "exhausted" in kwargs["result_message"].lower()

    async def test_provision_exception_is_classified_as_failure(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=3, publish_retry_count=0
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
            side_effect=RuntimeError("provider down")
        )
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is True
        svc._publish_record_repo.update_result.assert_not_called()


class TestAttemptLeavesRecordInFlight:
    async def test_retrying_record_stays_processing(self):
        svc = _make_service()
        rec = _make_record()
        started = await TestAttemptOutcomes()._run(
            svc, rec, DeviceOperationOutcome.HOOK_PENDING
        )
        assert started is True
        svc._publish_record_repo.update_result.assert_not_called()
        svc._publish_record_repo.update_result_if_processing.assert_not_called()


class TestProvisionDispatchByType:
    async def _run_with_type(self, publish_type: str):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=2, publish_retry_count=0
        )
        pub = _publish()
        pub.publish_type = publish_type
        svc._publish_repo.get_by_id.return_value = pub
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        response = MagicMock(operation_outcome=DeviceOperationOutcome.HOOK_PENDING)
        svc._device_service.start_device = AsyncMock(return_value=response)
        svc._device_service.restart_device = AsyncMock(return_value=response)
        svc._device_service.update_device = AsyncMock(return_value=response)
        with _patch_env():
            await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        return svc

    async def test_create_uses_start_device(self):
        svc = await self._run_with_type("CREATE")
        svc._device_service.start_device.assert_awaited_once()
        svc._device_service.restart_device.assert_not_called()

    async def test_restart_uses_restart_device(self):
        svc = await self._run_with_type("RESTART")
        svc._device_service.restart_device.assert_awaited_once()
        svc._device_service.start_device.assert_not_called()

    async def test_update_uses_update_device(self):
        svc = await self._run_with_type("UPDATE")
        svc._device_service.update_device.assert_awaited_once()

    async def test_update_device_uses_update_device(self):
        svc = await self._run_with_type("UPDATE_DEVICE")
        svc._device_service.update_device.assert_awaited_once()

    async def test_scale_up_uses_start_device(self):
        svc = await self._run_with_type("SCALE_UP")
        svc._device_service.start_device.assert_awaited_once()


class TestClaimPersistence:
    async def test_claim_is_taken_with_expected_count(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
            return_value=MagicMock(
                operation_outcome=DeviceOperationOutcome.HOOK_PENDING
            )
        )

        with _patch_env():
            await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )

        claim_kwargs = svc._publish_record_repo.try_claim_retry.call_args.kwargs
        assert claim_kwargs["expected_retry_count"] == 0
        assert claim_kwargs["attempt_started_at"]

    async def test_backoff_applied_on_later_attempt(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=3, publish_retry_count=1
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
            return_value=MagicMock(
                operation_outcome=DeviceOperationOutcome.HOOK_PENDING
            )
        )

        with (
            _patch_env(),
            patch(
                "secbaas.community.core.service.publish_manage"
                "._publish_retry_orchestrator.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleeper,
        ):
            await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )

        sleeper.assert_awaited_once()


class TestSweepRecordAttempt:
    async def test_sweep_claims_when_budget_remains(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=2, publish_retry_count=0
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
            return_value=MagicMock(
                operation_outcome=DeviceOperationOutcome.HOOK_PENDING
            )
        )
        with _patch_env():
            started = await svc.sweep_record_attempt(record=rec, tenant="t")
        assert started is True

    async def test_sweep_settles_when_budget_exhausted(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=2, publish_retry_count=2
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        with _patch_env():
            started = await svc.sweep_record_attempt(record=rec, tenant="t")
        assert started is False
        svc._publish_record_repo.try_claim_retry.assert_not_called()
        kwargs = svc._publish_record_repo.update_result.call_args.kwargs
        assert kwargs["result_status"] == "FAILED"

    async def test_sweep_does_not_duplicate_claimed_attempt(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=2, publish_retry_count=0
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = False
        with _patch_env():
            started = await svc.sweep_record_attempt(record=rec, tenant="t")
        assert started is False
        svc._device_service.start_device.assert_not_called()

    async def test_sweep_settles_record_with_zero_budget(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=0
        )
        with _patch_env():
            started = await svc.sweep_record_attempt(record=rec, tenant="t")
        assert started is False
        kwargs = svc._publish_record_repo.update_result.call_args.kwargs
        assert kwargs["result_status"] == "FAILED"

    async def test_sweep_leaves_fresh_attempt_in_flight(self):
        svc = _make_service()
        rec = _make_record()
        fresh = PublishRecordExtraConfig(
            publish_max_retry_times=3,
            publish_retry_count=1,
            attempt_started_at=attempt_started_at(),
        )
        svc._publish_record_repo.get_retry_state.return_value = fresh
        svc._publish_repo.get_by_id.return_value = _publish()
        with _patch_env():
            started = await svc.sweep_record_attempt(record=rec, tenant="t")
        assert started is False
        svc._publish_record_repo.update_result.assert_not_called()


class TestPerAttemptTimeout:
    async def test_unexpired_attempt_not_retried(self):
        svc = _make_service()
        rec = _make_record()
        fresh = PublishRecordExtraConfig(
            publish_max_retry_times=3,
            publish_retry_count=1,
            attempt_started_at=attempt_started_at(),
        )
        svc._publish_record_repo.get_retry_state.return_value = fresh
        svc._publish_repo.get_by_id.return_value = _publish()
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        svc._publish_record_repo.try_claim_retry.assert_not_called()

    async def test_expired_attempt_is_retryable(self):
        svc = _make_service()
        rec = _make_record()
        stale = PublishRecordExtraConfig(
            publish_max_retry_times=3,
            publish_retry_count=1,
            attempt_started_at="2020-01-01 00:00:00",
        )
        svc._publish_record_repo.get_retry_state.return_value = stale
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = False
        with _patch_env():
            await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        svc._publish_record_repo.try_claim_retry.assert_called_once()


class TestDeviceUuidResolution:
    async def test_uuid_taken_from_retry_state(self):
        svc = _make_service()
        rec = _make_record()
        rec.device_uuid = None
        svc._publish_record_repo.get_retry_state.return_value = (
            PublishRecordExtraConfig(
                device_uuid="dev-from-state",
                publish_max_retry_times=2,
                publish_retry_count=0,
            )
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = False
        with _patch_env():
            await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert rec.device_uuid == "dev-from-state"

    async def test_uuid_falls_back_to_device_row(self):
        svc = _make_service()
        rec = _make_record()
        rec.device_uuid = None
        rec.device_id = 42
        svc._publish_record_repo.get_retry_state.return_value = (
            PublishRecordExtraConfig(
                device_uuid=None, publish_max_retry_times=2, publish_retry_count=0
            )
        )
        svc._device_repo.get_by_id.return_value = MagicMock(device_uuid="dev-row")
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = False
        with _patch_env():
            await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert rec.device_uuid == "dev-row"

    async def test_unresolvable_uuid_aborts_retry(self):
        svc = _make_service()
        rec = _make_record()
        rec.device_uuid = None
        rec.device_id = None
        svc._publish_record_repo.get_retry_state.return_value = (
            PublishRecordExtraConfig(
                device_uuid=None, publish_max_retry_times=2, publish_retry_count=0
            )
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        svc._publish_record_repo.try_claim_retry.assert_not_called()

    async def test_missing_device_row_yields_none(self):
        svc = _make_service()
        rec = _make_record()
        rec.device_id = 42
        svc._device_repo.get_by_id.return_value = None
        state = PublishRecordExtraConfig(device_uuid=None, publish_max_retry_times=2)
        assert svc._resolve_record_device_uuid(rec, state, "t", "e") is None


class TestRetryAllowedBranches:
    def test_missing_publish_blocks_retry(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_repo.get_by_id.return_value = None
        assert (
            svc._retry_allowed(rec, _state(publish_max_retry_times=2), "t", "e")
            is False
        )

    def test_database_now_failure_falls_back_to_host_clock(self):
        svc = _make_service()
        rec = _make_record()
        svc._provider_supports_retry = MagicMock(return_value=True)
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.database_now.side_effect = RuntimeError("db down")

        with patch(
            "secbaas.community.core.service.publish_manage"
            "._publish_retry_orchestrator.naive_cst_now",
            return_value=datetime(2026, 1, 1, 12, 0, 0),
        ) as fallback:
            result = svc._retry_allowed(
                rec, _state(publish_max_retry_times=2), "t", "e"
            )

        fallback.assert_called_once()
        assert result is True

    def test_non_dict_extra_config_uses_default_timeout(self):
        svc = _make_service()
        rec = _make_record()
        pub = _publish()
        pub.extra_config = "not-a-dict"
        svc._publish_repo.get_by_id.return_value = pub
        assert svc._attempt_timeout_seconds(rec, "t", "e") == 1800

    def test_empty_extra_config_uses_default_timeout(self):
        svc = _make_service()
        rec = _make_record()
        pub = _publish()
        pub.extra_config = {}
        svc._publish_repo.get_by_id.return_value = pub
        assert svc._attempt_timeout_seconds(rec, "t", "e") == 1800

    def test_configured_timeout_is_used(self):
        svc = _make_service()
        rec = _make_record()
        pub = _publish()
        pub.extra_config = {"callback_timeout_seconds": 120}
        svc._publish_repo.get_by_id.return_value = pub
        assert svc._attempt_timeout_seconds(rec, "t", "e") == 120


class TestResetDeviceBranches:
    async def test_missing_device_blocks_retry(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = None
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        svc._device_service.start_device.assert_not_called()

    async def test_prepare_failure_finalizes_record(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = False
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        kwargs = svc._publish_record_repo.update_result.call_args.kwargs
        assert kwargs["result_status"] == "FAILED"

    async def test_missing_uuid_in_reset_returns_false(self):
        svc = _make_service()
        rec = _make_record(device_uuid=None)
        assert await svc._reset_device_for_retry(rec, "t", "op") is False


class TestFinalizeSuccessRace:
    async def test_already_settled_record_is_not_reconciled_twice(self):
        svc = _make_service()
        rec = _make_record()
        rec.batch_id = 5
        svc._publish_record_repo.update_result_if_processing.return_value = False
        svc._check_batch_completion = AsyncMock()

        await svc._finalize_record_success(rec, "t", "op")

        svc._check_batch_completion.assert_not_awaited()

    async def test_success_without_batch_id_skips_completion(self):
        svc = _make_service()
        rec = _make_record(batch_id=None)
        svc._publish_record_repo.update_result_if_processing.return_value = True
        svc._check_batch_completion = AsyncMock()

        await svc._finalize_record_success(rec, "t", "op")

        svc._check_batch_completion.assert_not_awaited()


class TestSweepUnusableState:
    async def test_sweep_with_unusable_state_is_noop(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = None
        with _patch_env():
            started = await svc.sweep_record_attempt(record=rec, tenant="t")
        assert started is False
        svc._publish_record_repo.try_claim_retry.assert_not_called()
        svc._publish_record_repo.update_result.assert_not_called()

    async def test_sweep_with_malformed_state_is_noop(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = MagicMock()
        with _patch_env():
            started = await svc.sweep_record_attempt(record=rec, tenant="t")
        assert started is False
        svc._publish_record_repo.try_claim_retry.assert_not_called()


class TestPublishRetryTimesBounds:
    def test_publish_config_accepts_zero_through_three(self):
        for value in (0, 1, 2, 3):
            assert (
                PublishConfig(publish_max_retry_times=value).publish_max_retry_times
                == value
            )

    def test_publish_config_rejects_above_three(self):
        for value in (4, 10):
            with pytest.raises(Exception):
                PublishConfig(publish_max_retry_times=value)

    def test_publish_config_rejects_negative(self):
        with pytest.raises(Exception):
            PublishConfig(publish_max_retry_times=-1)

    def test_bot_config_accepts_zero_through_three(self):
        for value in (0, 1, 2, 3):
            assert (
                BotConfig(publish_max_retry_times=value).publish_max_retry_times
                == value
            )

    def test_bot_config_rejects_above_three(self):
        for value in (4, 10):
            with pytest.raises(Exception):
                BotConfig(publish_max_retry_times=value)

    def test_bot_config_defaults_to_unset(self):
        assert BotConfig().publish_max_retry_times is None


class TestNotAttemptedOutcome:
    async def test_not_attempted_does_not_consume_retry_budget(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=3, publish_retry_count=0
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
            return_value=MagicMock(
                operation_outcome=DeviceOperationOutcome.NOT_ATTEMPTED
            )
        )
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        kwargs = svc._publish_record_repo.update_result.call_args.kwargs
        assert kwargs["result_status"] == "FAILED"
        assert "no retryable outcome" in kwargs["result_message"]

    async def test_hook_failed_outcome_is_retryable(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=3, publish_retry_count=0
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(
            return_value=MagicMock(operation_outcome=DeviceOperationOutcome.HOOK_FAILED)
        )
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is True
        svc._publish_record_repo.update_result.assert_not_called()

    async def test_missing_outcome_attribute_is_unclassified(self):
        svc = _make_service()
        rec = _make_record()
        svc._publish_record_repo.get_retry_state.return_value = _state()
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id=None
        )
        svc._device_service.prepare_for_reprovision.return_value = True
        svc._device_service.start_device = AsyncMock(return_value=MagicMock())
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=rec,
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False


class TestHookFailedReProvisions:
    async def test_hook_failed_reprovisions_and_dispatches_hook_again(self):
        svc = _make_service()
        rec = _make_record()
        order: list[str] = []

        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=2, publish_retry_count=0
        )
        svc._publish_repo.get_by_id.return_value = _publish()
        svc._publish_record_repo.try_claim_retry.return_value = True
        svc._device_repo.get_by_device_uuid.return_value = MagicMock(
            provider_device_id="prov-old"
        )

        async def _destroy(**_):
            order.append("destroy")

        svc._device_service.destroy_device_by_uuid.side_effect = _destroy

        def _prepare(**_):
            order.append("recover-to-pending")
            return True

        svc._device_service.prepare_for_reprovision.side_effect = _prepare

        async def _start(**_):
            order.append("create-sandbox+dispatch-hook")
            return MagicMock(operation_outcome=DeviceOperationOutcome.HOOK_PENDING)

        svc._device_service.start_device.side_effect = _start

        retried = await svc._drive_record_attempt(
            record=rec,
            tenant="t",
            operator="callback",
            publish_id=9,
            failure_reason="hook exited 1",
        )

        assert retried is True
        assert order == [
            "destroy",
            "recover-to-pending",
            "create-sandbox+dispatch-hook",
        ]
        svc._publish_record_repo.update_result.assert_not_called()
        svc._publish_record_repo.update_result_if_processing.assert_not_called()


class TestProviderSupportsRetry:
    """Retry is limited to ARCA; other platforms settle as before."""

    def _svc_with_template(self, config):
        svc = _make_service()
        svc._template_service.get_default_or_explicit_template.return_value = (
            MagicMock(config=config) if config is not None else None
        )
        return svc

    def test_arca_template_is_eligible(self):
        svc = self._svc_with_template(ArcaTemplateConfig.model_construct())
        assert svc._provider_supports_retry(_make_record(), "t", "e") is True

    @pytest.mark.parametrize(
        "config",
        [
            LocalTemplateConfig.model_construct(),
            SigmaTemplateConfig.model_construct(),
            PoolabTemplateConfig.model_construct(),
            TeClawTemplateConfig.model_construct(),
            K8sTemplateConfig.model_construct(),
            DockerTemplateConfig.model_construct(),
        ],
    )
    def test_other_templates_are_not_eligible(self, config):
        svc = self._svc_with_template(config)
        assert svc._provider_supports_retry(_make_record(), "t", "e") is False

    def test_missing_device_is_not_eligible(self):
        svc = _make_service()
        svc._device_repo.get_by_device_uuid.return_value = None
        assert svc._provider_supports_retry(_make_record(), "t", "e") is False

    def test_template_lookup_failure_disables_retry(self):
        svc = _make_service()
        svc._template_service.get_default_or_explicit_template.side_effect = (
            RuntimeError("db down")
        )
        assert svc._provider_supports_retry(_make_record(), "t", "e") is False

    def test_template_without_config_is_not_eligible(self):
        svc = self._svc_with_template(None)
        assert svc._provider_supports_retry(_make_record(), "t", "e") is False

    async def test_non_arca_failure_does_not_consume_retry(self):
        svc = self._svc_with_template(LocalTemplateConfig.model_construct())
        svc._publish_record_repo.get_retry_state.return_value = _state(
            publish_max_retry_times=3, publish_retry_count=0
        )
        with _patch_env():
            started = await svc._drive_record_attempt(
                record=_make_record(),
                tenant="t",
                operator="op",
                publish_id=9,
                failure_reason="boom",
            )
        assert started is False
        svc._publish_record_repo.try_claim_retry.assert_not_called()
