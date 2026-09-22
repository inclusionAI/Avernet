"""Per-device publish attempt orchestrator.

Owns exactly one attempt of the per-device publish lifecycle — destroy any
existing sandbox, recover the device, re-provision, persist the new provider
identity, and dispatch the start hook — for every publish type. The
orchestrator is stateless and DB-driven so it can be re-entered from the
inline batch failure path, the callback handler, and the periodic sweep.

Retry decisions are serialized by an atomic claim on the publish record's
``publish_retry_count``; a caller that loses the claim must not touch the provider.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from secbaas.community.api.device_manage import (
    DeviceOperationOutcome,
    DeviceStatus,
)
from secbaas.community.api.publish_manage import (
    DEFAULT_CALLBACK_TIMEOUT_SECONDS,
    PublishRecordResult,
    PublishType,
)
from secbaas.community.core.repository.publish_record import (
    PublishRecordExtraConfig,
)
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.core.utils.provision_retry import (
    attempt_started_at,
    is_attempt_expired,
    is_publish_deadline_exceeded,
    retry_backoff_seconds,
)
from secbaas.community.core.utils.time_utils import naive_cst_now
from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from secbaas.community.core.repository.publish_record import (
        PublishRecordRecord,
    )

logger = get_logger("core-service")

MAX_PUBLISH_DURATION_SECONDS = 3600
_RETRYABLE_PROVIDER_TYPES = frozenset({"ARCA"})
_TERMINAL_STATUSES = {
    PublishRecordResult.SUCCESS.value,
    PublishRecordResult.FAILED.value,
}
_SUCCESS_OUTCOMES = {
    DeviceOperationOutcome.HOOK_PENDING,
    DeviceOperationOutcome.COMPLETED,
}
_RETRYABLE_OUTCOMES = {
    DeviceOperationOutcome.PROVISION_FAILED,
    DeviceOperationOutcome.HOOK_FAILED,
}


class PublishAttemptOrchestrator:
    """Mixin providing the per-device publish attempt orchestrator.

    Expects the host class to expose the same repository and service
    attributes as ``DefaultPublishService``.
    """

    async def _drive_record_attempt(
        self,
        *,
        record: PublishRecordRecord,
        tenant: str,
        operator: str,
        publish_id: int,
        failure_reason: str,
    ) -> bool:
        """Claim and perform one retry attempt for a device publish record.

        Returns True when a new attempt was started. Returns False when the
        record is not retryable, the budget is exhausted, another caller holds
        the claim, or the publish deadline has passed — in which case the
        record is left in its terminal failed state.
        """
        env = get_current_env()
        record_repo = self._publish_record_repo

        if record.result_status in _TERMINAL_STATUSES:
            return False

        state = record_repo.get_retry_state(record.id, tenant, env)
        if not _is_usable_retry_state(state):
            return False

        if not record.device_uuid:
            record.device_uuid = self._resolve_record_device_uuid(
                record, state, tenant, env
            )
        if not record.device_uuid:
            logger.error(
                f"[retry] cannot resolve device_uuid for record={record.id}; "
                f"retry aborted"
            )
            return False

        if not self._retry_allowed(record, state, tenant, env):
            return False

        claimed = record_repo.try_claim_retry(
            record_id=record.id,
            tenant=tenant,
            env=env,
            expected_retry_count=state.publish_retry_count,
            attempt_started_at=attempt_started_at(),
            modifier=operator,
        )
        if not claimed:
            logger.info(
                f"[retry] attempt already claimed by another signal: "
                f"record={record.id} device_uuid={record.device_uuid}"
            )
            return False

        attempt_ordinal = state.publish_retry_count + 1
        logger.warning(
            f"[retry] starting attempt {attempt_ordinal}/{state.publish_max_retry_times} "
            f"for record={record.id} device_uuid={record.device_uuid} "
            f"after failure: {failure_reason}"
        )

        if attempt_ordinal > 1:
            await asyncio.sleep(retry_backoff_seconds(attempt_ordinal - 1))

        if not await self._reset_device_for_retry(record, tenant, operator):
            await self._finalize_record_failure(
                record=record,
                tenant=tenant,
                operator=operator,
                reason="Device could not be reset for retry",
            )
            return False

        outcome = await self._provision_record_attempt(
            record=record,
            tenant=tenant,
            operator=operator,
            publish_id=publish_id,
        )

        if outcome in _SUCCESS_OUTCOMES:
            if outcome is DeviceOperationOutcome.COMPLETED:
                await self._finalize_record_success(record, tenant, operator)
            return True

        if outcome not in _RETRYABLE_OUTCOMES:
            await self._finalize_record_failure(
                record=record,
                tenant=tenant,
                operator=operator,
                reason=f"Attempt produced no retryable outcome: {outcome}",
            )
            return False

        if attempt_ordinal >= state.publish_max_retry_times:
            await self._finalize_record_failure(
                record=record,
                tenant=tenant,
                operator=operator,
                reason=f"Retries exhausted after {attempt_ordinal} attempts",
            )
            return False

        return True

    def _resolve_record_device_uuid(
        self,
        record: PublishRecordRecord,
        state: PublishRecordExtraConfig,
        tenant: str,
        env: str,
    ) -> str | None:
        if state.device_uuid:
            return state.device_uuid
        if record.device_id:
            device = self._device_repo.get_by_id(record.device_id, tenant, env)
            if device is not None:
                return device.device_uuid
        return None

    def _retry_allowed(
        self,
        record: PublishRecordRecord,
        state: PublishRecordExtraConfig,
        tenant: str,
        env: str,
    ) -> bool:
        if state.publish_max_retry_times <= 0:
            return False
        if state.publish_retry_count >= state.publish_max_retry_times:
            return False
        if not self._provider_supports_retry(record, tenant, env):
            return False
        if state.publish_retry_count > 0 and not is_attempt_expired(
            state.attempt_started_at, self._attempt_timeout_seconds(record, tenant, env)
        ):
            return False

        publish = self._publish_repo.get_by_id(
            record.publish_id, tenant=tenant, env=env
        )
        if publish is None:
            return False
        try:
            reference = self._publish_record_repo.database_now()
        except Exception:
            reference = naive_cst_now()
        return not is_publish_deadline_exceeded(
            publish.gmt_create, MAX_PUBLISH_DURATION_SECONDS, now=reference
        )

    def _attempt_timeout_seconds(
        self, record: PublishRecordRecord, tenant: str, env: str
    ) -> int:
        from ._publish_service import (
            _extra_config_to_publish_config,
        )

        publish = self._publish_repo.get_by_id(
            record.publish_id, tenant=tenant, env=env
        )
        raw = getattr(publish, "extra_config", None)
        if not isinstance(raw, dict):
            return DEFAULT_CALLBACK_TIMEOUT_SECONDS
        config = _extra_config_to_publish_config(raw)
        if config is None:
            return DEFAULT_CALLBACK_TIMEOUT_SECONDS
        return config.callback_timeout_seconds

    def _provider_supports_retry(
        self, record: PublishRecordRecord, tenant: str, env: str
    ) -> bool:
        """Whether the device's platform is eligible for publish retry.

        Retry re-provisions the sandbox, which is only validated for ARCA.
        The template is consulted rather than the device row because a
        provision failure returns before the provider type is persisted.
        """
        device = self._device_repo.get_by_device_uuid(
            device_uuid=record.device_uuid, tenant=tenant, env=env, status=None
        )
        if device is None:
            return False
        return self._template_is_arca(device, tenant)

    def _template_is_arca(self, device: object, tenant: str) -> bool:
        from secbaas.community.api.template_manage import ArcaTemplateConfig

        raw = getattr(device, "extra_config", None)
        template_uuid = raw.get("template_uuid") if isinstance(raw, dict) else None
        try:
            template = self._template_service.get_default_or_explicit_template(
                tenant=tenant, template_uuid=template_uuid
            )
        except Exception:
            logger.warning(
                f"[retry] template lookup failed for device="
                f"{getattr(device, 'device_uuid', None)}; retry disabled"
            )
            return False
        if template is None or template.config is None:
            return False
        return isinstance(template.config, ArcaTemplateConfig)

    async def _reset_device_for_retry(
        self,
        record: PublishRecordRecord,
        tenant: str,
        operator: str,
    ) -> bool:
        env = get_current_env()
        if not record.device_uuid:
            return False

        device = self._device_repo.get_by_device_uuid(
            device_uuid=record.device_uuid, tenant=tenant, env=env, status=None
        )
        if device is None:
            return False

        if device.provider_device_id:
            destroyed = await self._destroy_retry_sandbox(
                record=record,
                provider_device_id=device.provider_device_id,
                tenant=tenant,
                operator=operator,
            )
            if not destroyed:
                logger.error(
                    f"[retry] destroy failed; refusing to re-provision: "
                    f"device_uuid={record.device_uuid}"
                )
                return False

        return self._device_service.prepare_for_reprovision(
            tenant=tenant, device_uuid=record.device_uuid, modifier=operator
        )

    async def _destroy_retry_sandbox(
        self,
        *,
        record: PublishRecordRecord,
        provider_device_id: str,
        tenant: str,
        operator: str,
    ) -> bool:
        try:
            await self._device_service.destroy_device_by_uuid(
                tenant=tenant,
                device_uuid=record.device_uuid,
                modifier=operator,
                for_restart=True,
            )
            logger.info(
                f"[retry] destroyed existing sandbox: "
                f"device_uuid={record.device_uuid} provider={provider_device_id}"
            )
            return True
        except Exception as exc:
            if _is_not_found(exc):
                logger.info(
                    f"[retry] sandbox already absent, treating as destroyed: "
                    f"device_uuid={record.device_uuid}"
                )
                return True
            logger.exception(
                f"[retry] sandbox destroy raised for device_uuid={record.device_uuid}"
            )
            return False

    async def _provision_record_attempt(
        self,
        *,
        record: PublishRecordRecord,
        tenant: str,
        operator: str,
        publish_id: int,
    ) -> DeviceOperationOutcome:
        publish = self._publish_repo.get_by_id(
            record.publish_id, tenant=tenant, env=get_current_env()
        )
        publish_type = getattr(publish, "publish_type", None)

        try:
            if publish_type == PublishType.RESTART.value:
                response = await self._device_service.restart_device(
                    tenant=tenant,
                    device_uuid=record.device_uuid,
                    modifier=operator,
                    publish_id=publish_id,
                )
            elif publish_type in (
                PublishType.UPDATE.value,
                PublishType.UPDATE_DEVICE.value,
            ):
                response = await self._device_service.update_device(
                    tenant=tenant,
                    device_uuid=record.device_uuid,
                    modifier=operator,
                    publish_id=publish_id,
                )
            else:
                response = await self._device_service.start_device(
                    tenant=tenant,
                    device_uuid=record.device_uuid,
                    modifier=operator,
                    publish_id=publish_id,
                )
        except Exception:
            logger.exception(
                f"[retry] provision attempt raised: device_uuid={record.device_uuid}"
            )
            return DeviceOperationOutcome.PROVISION_FAILED

        return getattr(
            response,
            "operation_outcome",
            DeviceOperationOutcome.NOT_ATTEMPTED,
        )

    async def _finalize_record_failure(
        self,
        *,
        record: PublishRecordRecord,
        tenant: str,
        operator: str,
        reason: str,
    ) -> None:
        env = get_current_env()
        self._publish_record_repo.update_result(
            record_id=record.id,
            tenant=tenant,
            env=env,
            result_status=PublishRecordResult.FAILED.value,
            result_message=reason[:4000],
            modifier=operator,
        )
        self._device_repo.update_status_by_device_uuid(
            device_uuid=record.device_uuid,
            tenant=tenant,
            env=env,
            status=DeviceStatus.FAILED.value,
            modifier=operator,
        )
        logger.error(
            f"[retry] record finalized FAILED: record={record.id} "
            f"device_uuid={record.device_uuid} reason={reason}"
        )

    async def _finalize_record_success(
        self,
        record: PublishRecordRecord,
        tenant: str,
        operator: str,
    ) -> None:
        env = get_current_env()
        updated = self._publish_record_repo.update_result_if_processing(
            record_id=record.id,
            tenant=tenant,
            env=env,
            result_status=PublishRecordResult.SUCCESS.value,
            result_message="Device started successfully after retry",
            modifier=operator,
        )
        if not updated:
            logger.info(f"[retry] record already settled: record={record.id}")
            return
        logger.info(
            f"[retry] record finalized SUCCESS after retry: record={record.id} "
            f"device_uuid={record.device_uuid}"
        )
        if record.batch_id is not None:
            await self._check_batch_completion(
                tenant=tenant,
                batch_id=record.batch_id,
                publish_id=record.publish_id,
            )

    async def sweep_record_attempt(
        self, *, record: PublishRecordRecord, tenant: str
    ) -> bool:
        """Entry point for the server-side sweep.

        Returns True when the sweep started a new attempt for the record.
        """
        env = get_current_env()
        state = self._publish_record_repo.get_retry_state(record.id, tenant, env)
        if not _is_usable_retry_state(state):
            return False

        if not self._retry_allowed(record, state, tenant, env):
            exhausted = state.publish_retry_count >= state.publish_max_retry_times
            expired = state.publish_retry_count == 0 or is_attempt_expired(
                state.attempt_started_at,
                self._attempt_timeout_seconds(record, tenant, env),
            )
            if exhausted and expired:
                await self._finalize_record_failure(
                    record=record,
                    tenant=tenant,
                    operator="sweep",
                    reason=(
                        f"Retries exhausted after {state.publish_retry_count} attempts "
                        f"(attempt window elapsed)"
                    ),
                )
            return False

        return await self._drive_record_attempt(
            record=record,
            tenant=tenant,
            operator="sweep",
            publish_id=record.publish_id,
            failure_reason="Attempt window elapsed",
        )


def _is_not_found(exc: Exception) -> bool:
    text = str(exc).lower()
    return "not_found" in text or "not found" in text


def _is_usable_retry_state(state: object) -> bool:
    if not isinstance(state, PublishRecordExtraConfig):
        return False
    return isinstance(state.publish_max_retry_times, int) and isinstance(
        state.publish_retry_count, int
    )


def _retry_field(record: object, field_name: str) -> int:
    raw = (getattr(record, "extra_config", None) or {}).get(field_name)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
