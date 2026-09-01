"""Run one durable SC Public Reference batch through exact materialization."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import time

from agentclaw.community.core.repository.protocols.skill_center_reference import (
    SkillCenterReferenceRepositoryProtocol,
)
from agentclaw.community.core.repository.skill_center_reference_types import (
    SkillCenterReferenceWorkBatch,
    SkillCenterReferenceWorkItem,
)
from agentclaw.community.core.skill_center.errors import (
    SkillOfflineError,
    SkillSetAccessDeniedError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    SkillVersionMaterializationError,
    SkillVersionMaterializationRequest,
    SkillVersionMaterializerProtocol,
)
from agentclaw.community.core.skill_center.reference_contract import (
    SkillCenterReferenceStatus,
    TERMINAL_REFERENCE_STATUSES,
)
from agentclaw.community.core.skill_center.public_center_identity import (
    PublicCenterSkillIdentity,
)
from agentclaw.community.core.skill_center.skill_center_gateway_service_protocol import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_set_management_service_protocol import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.core.skill_center.track_latest_service_protocol import (
    TrackLatestServiceProtocol,
)
from agentclaw.community.core.task_queue.types import Complete, Fail, Retry, TaskOutcome
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterGatewayError,
    SkillCenterGatewayErrorCode,
    SkillCenterPublicSkillDetailRequest,
    SkillCenterReadScope,
    SkillCenterVersionListRequest,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.log import get_logger


_MAX_ITEM_ATTEMPTS = 3
logger = get_logger()

_PUBLIC_REFERENCE_ERRORS = {
    "SC_SKILL_NOT_FOUND": "Skill Center Skill is not available",
    "SC_MARKET_UNAVAILABLE": "Skill Center is temporarily unavailable",
    "MATERIALIZATION_FAILED": "Exact Version materialization failed",
    "SKILL_OFFLINE": "Skill is offline",
    "SKILL_SET_NOT_FOUND": "SkillSet is not available",
    "RUNTIME_PROJECTION_FAILED": "Runtime projection failed",
    "SKILL_SET_FORBIDDEN": "Forbidden",
    "SKILL_SET_UPDATE_FAILED": "SkillSet update failed",
}


def _duration_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


class SkillCenterReferenceProcessor:
    """Materialize independently, then commit successful members in one batch."""

    def __init__(
        self,
        *,
        references: SkillCenterReferenceRepositoryProtocol,
        gateway: SkillCenterGatewayServiceProtocol,
        materializer: SkillVersionMaterializerProtocol,
        skill_sets: SkillSetManagementServiceProtocol,
        track_latest: TrackLatestServiceProtocol,
        env_provider: Callable[[], str] = get_current_env,
        max_concurrency: int = 4,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self._references = references
        self._gateway = gateway
        self._materializer = materializer
        self._skill_sets = skill_sets
        self._track_latest = track_latest
        self._env_provider = env_provider
        self._max_concurrency = max_concurrency

    async def process(self, request_id: str) -> TaskOutcome:
        batch_started_at = time.perf_counter()
        batch = self._references.get_work_batch(
            env=self._env_provider(), request_id=request_id
        )
        if batch is None:
            return Fail(f"Reference batch not found: {request_id}")
        pending = [
            item
            for item in batch.items
            if item.status
            not in TERMINAL_REFERENCE_STATUSES
            and item.status
            not in {
                SkillCenterReferenceStatus.ADDING_TO_SKILL_SET,
                SkillCenterReferenceStatus.PROJECTING_RUNTIME,
            }
        ]
        logger.info(
            "[SkillCenterReference] batch materialization started: request_id=%s "
            "env=%s item_count=%s pending_count=%s max_concurrency=%s",
            batch.request_id,
            batch.env,
            len(batch.items),
            len(pending),
            self._max_concurrency,
        )
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def run(item: SkillCenterReferenceWorkItem) -> bool:
            async with semaphore:
                return await asyncio.to_thread(self._materialize_one, batch, item)

        materialization_started_at = time.perf_counter()
        retry_required = any(await asyncio.gather(*(run(item) for item in pending)))
        logger.info(
            "[SkillCenterReference] batch materialization barrier completed: "
            "request_id=%s env=%s pending_count=%s retry_required=%s duration_ms=%s",
            batch.request_id,
            batch.env,
            len(pending),
            retry_required,
            _duration_ms(materialization_started_at),
        )
        if retry_required:
            logger.info(
                "[SkillCenterReference] batch processing deferred for retry: "
                "request_id=%s env=%s duration_ms=%s",
                batch.request_id,
                batch.env,
                _duration_ms(batch_started_at),
            )
            return Retry("one or more Reference items need another attempt")

        latest = self._references.get_work_batch(
            env=batch.env, request_id=batch.request_id
        )
        assert latest is not None
        ready = [
            item
            for item in latest.items
            if item.status
            in {
                SkillCenterReferenceStatus.ADDING_TO_SKILL_SET,
                SkillCenterReferenceStatus.PROJECTING_RUNTIME,
            }
        ]
        if not ready:
            logger.info(
                "[SkillCenterReference] batch processing completed without final add: "
                "request_id=%s env=%s duration_ms=%s",
                batch.request_id,
                batch.env,
                _duration_ms(batch_started_at),
            )
            return Complete()
        await self._add_ready(latest, ready)
        logger.info(
            "[SkillCenterReference] batch processing completed: request_id=%s env=%s "
            "ready_count=%s duration_ms=%s",
            batch.request_id,
            batch.env,
            len(ready),
            _duration_ms(batch_started_at),
        )
        return Complete()

    def _materialize_one(
        self,
        batch: SkillCenterReferenceWorkBatch,
        item: SkillCenterReferenceWorkItem,
    ) -> bool:
        started_at = time.perf_counter()
        current = self._references.update_item(
            env=batch.env,
            reference_id=item.reference_id,
            status=SkillCenterReferenceStatus.RESOLVING_VERSION,
        )
        try:
            detail = self._gateway.get_public_skill(
                SkillCenterPublicSkillDetailRequest(item.skill_code)
            )
            if (
                detail is None
                or detail.skill_id is None
                or not detail.latest_version_number
            ):
                raise _PermanentReferenceError(
                    "SC_SKILL_NOT_FOUND", "public Skill has no consumable latest Version"
                )
            versions = self._gateway.list_versions(
                SkillCenterVersionListRequest(
                    skill_code=item.skill_code,
                    scope=SkillCenterReadScope.PUBLIC,
                )
            )
            exact = next(
                (
                    version
                    for version in versions
                    if version.version_number == detail.latest_version_number
                ),
                None,
            )
            if exact is None or exact.version_id is None:
                raise _PermanentReferenceError(
                    "SC_SKILL_NOT_FOUND", "latest public Version has no exact identity"
                )
            identity = PublicCenterSkillIdentity.derive(
                tenant=get_current_avernet_tenant(),
                env=batch.env,
                skill_code=item.skill_code,
            )
            target = self._references.ensure_public_version(
                env=batch.env,
                actor_id=batch.actor_id,
                locator=identity.locator,
                skill_uuid=identity.skill_uuid,
                skill_name=detail.skill_name,
                description=detail.description,
                sc_skill_id=_positive_int(detail.skill_id, "skill_id"),
                sc_version_number=exact.version_number,
                sc_version_id=_positive_int(exact.version_id, "version_id"),
            )
            current = self._references.update_item(
                env=batch.env,
                reference_id=item.reference_id,
                status=SkillCenterReferenceStatus.MATERIALIZING,
                sc_version_number=exact.version_number,
                skill_version_id=target.skill_version_id,
                resolved_skill_id=target.skill_id,
                error_code=None,
                error_message=None,
            )
            resolution_ms = _duration_ms(started_at)
            materialization_started_at = time.perf_counter()
            published = self._materializer.materialize(
                SkillVersionMaterializationRequest(
                    env=batch.env,
                    skill_id=target.skill_id,
                    skill_version_id=target.skill_version_id,
                    scope=SkillCenterReadScope.PUBLIC,
                )
            )
            materialization_ms = _duration_ms(materialization_started_at)
            # Re-ensure the level-triggered task even when another Reference
            # already published this exact Version.  This closes the accepted
            # post-commit enqueue window without creating a second event model.
            track_latest_started_at = time.perf_counter()
            self._track_latest.version_published(published)
            track_latest_ms = _duration_ms(track_latest_started_at)
            self._references.update_item(
                env=batch.env,
                reference_id=item.reference_id,
                status=SkillCenterReferenceStatus.ADDING_TO_SKILL_SET,
                sc_version_number=exact.version_number,
                skill_version_id=target.skill_version_id,
                resolved_skill_id=target.skill_id,
                error_code=None,
                error_message=None,
            )
            logger.info(
                "[SkillCenterReference] item materialized: request_id=%s env=%s "
                "reference_id=%s skill_code=%s skill_id=%s skill_version_id=%s "
                "resolution_ms=%s materialization_ms=%s track_latest_ms=%s duration_ms=%s",
                batch.request_id,
                batch.env,
                item.reference_id,
                item.skill_code,
                target.skill_id,
                target.skill_version_id,
                resolution_ms,
                materialization_ms,
                track_latest_ms,
                _duration_ms(started_at),
            )
            return False
        except _PermanentReferenceError as exc:
            logger.info(
                "[SkillCenterReference] permanent resolution failure: reference_id=%s code=%s",
                item.reference_id,
                exc.code,
            )
            self._fail(batch.env, item.reference_id, exc.code)
            return False
        except SkillCenterGatewayError as exc:
            logger.warning(
                "[SkillCenterReference] gateway failure: operation=reference_resolution "
                "env=%s scope=public reference_id=%s skill_code=%s skill_version_id=%s "
                "gateway_error_code=%s upstream_code=%s upstream_trace_id=%s",
                batch.env,
                item.reference_id,
                item.skill_code,
                current.skill_version_id,
                exc.code,
                exc.upstream_code,
                exc.trace_id,
            )
            if exc.code is SkillCenterGatewayErrorCode.BUSINESS:
                self._fail(batch.env, item.reference_id, "SC_SKILL_NOT_FOUND")
                return False
            return self._retry_or_fail(
                batch.env, current, "SC_MARKET_UNAVAILABLE"
            )
        except SkillVersionMaterializationError as exc:
            # Do not log the exception text or traceback here: download errors
            # can embed an exact-package presigned URL.  The materializer's
            # finite stage plus the exception type remain sufficient to locate
            # the failed Ready Gate without exposing credentials.
            cause = exc.__cause__
            logger.warning(
                "[SkillCenterReference] materialization failure: operation=reference_materialization "
                "env=%s scope=public reference_id=%s skill_code=%s skill_version_id=%s "
                "failure_stage=%s cause_type=%s duration_ms=%s",
                batch.env,
                item.reference_id,
                item.skill_code,
                current.skill_version_id,
                exc.stage or "unknown",
                type(cause).__name__ if cause is not None else type(exc).__name__,
                _duration_ms(started_at),
            )
            return self._retry_or_fail(
                batch.env, current, "MATERIALIZATION_FAILED"
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            logger.warning(
                "[SkillCenterReference] invalid materialization facts: "
                "operation=reference_materialization env=%s scope=public "
                "reference_id=%s skill_code=%s skill_version_id=%s failure_type=%s duration_ms=%s",
                batch.env,
                item.reference_id,
                item.skill_code,
                current.skill_version_id,
                type(exc).__name__,
                _duration_ms(started_at),
            )
            self._fail(batch.env, item.reference_id, "MATERIALIZATION_FAILED")
            return False

    async def _add_ready(
        self,
        batch: SkillCenterReferenceWorkBatch,
        ready: list[SkillCenterReferenceWorkItem],
    ) -> None:
        started_at = time.perf_counter()
        skill_ids = tuple(
            sorted((str(item.resolved_skill_id) for item in ready), key=int)
        )
        try:
            target = self._skill_sets.get_set(
                bot_id=batch.bot_id,
                owner_id=batch.owner_id,
                user_id=batch.actor_id,
                set_id=batch.skill_set_id,
            )
            if bool(target.get("is_active") or target.get("is_default")):
                for item in ready:
                    self._references.update_item(
                        env=batch.env,
                        reference_id=item.reference_id,
                        status=SkillCenterReferenceStatus.PROJECTING_RUNTIME,
                    )
            logger.info(
                "[SkillCenterReference] final SkillSet add started: request_id=%s env=%s "
                "skill_set_id=%s ready_count=%s runtime_projection_required=%s",
                batch.request_id,
                batch.env,
                batch.skill_set_id,
                len(ready),
                bool(target.get("is_active") or target.get("is_default")),
            )
            outcomes = await self._skill_sets.add_skills(
                bot_id=batch.bot_id,
                owner_id=batch.owner_id,
                user_id=batch.actor_id,
                set_id=batch.skill_set_id,
                # Materialization runs concurrently, so local IDs may become
                # ready in a different order on each replay. The batch has set
                # semantics, but sorting its numeric IDs keeps the wire and
                # idempotent observability stable.
                skill_ids=skill_ids,
            )
        except (
            SkillOfflineError,
            SkillSetAccessDeniedError,
            SkillSetControlPlaneConflictError,
            SkillSetControlPlaneNotFoundError,
            SkillSetRuntimeReconcileError,
        ) as exc:
            logger.exception(
                "[SkillCenterReference] final SkillSet add failed: request_id=%s duration_ms=%s",
                batch.request_id,
                _duration_ms(started_at),
            )
            code = _final_add_error_code(exc)
            for item in ready:
                self._fail(batch.env, item.reference_id, code)
            return

        by_skill_id = {outcome.skill_id: outcome for outcome in outcomes}
        succeeded_count = 0
        for item in ready:
            skill_id = str(item.resolved_skill_id)
            outcome = by_skill_id.get(skill_id)
            if outcome is None or not outcome.succeeded:
                error = outcome.error if outcome is not None else None
                logger.warning(
                    "[SkillCenterReference] SkillSet add omitted/failed item: reference_id=%s error=%r",
                    item.reference_id,
                    error,
                )
                self._fail(
                    batch.env,
                    item.reference_id,
                    _final_add_error_code(error),
                )
                continue
            self._references.update_item(
                env=batch.env,
                reference_id=item.reference_id,
                status=SkillCenterReferenceStatus.COMPLETED,
                error_code=None,
                error_message=None,
            )
            succeeded_count += 1
        logger.info(
            "[SkillCenterReference] final SkillSet add completed: request_id=%s env=%s "
            "skill_set_id=%s ready_count=%s succeeded_count=%s skill_ids=%s duration_ms=%s",
            batch.request_id,
            batch.env,
            batch.skill_set_id,
            len(ready),
            succeeded_count,
            skill_ids,
            _duration_ms(started_at),
        )

    def _retry_or_fail(
        self,
        env: str,
        item: SkillCenterReferenceWorkItem,
        code: str,
    ) -> bool:
        attempts = item.attempt_count + 1
        status = (
            SkillCenterReferenceStatus.FAILED
            if attempts >= _MAX_ITEM_ATTEMPTS
            else item.status
        )
        self._references.update_item(
            env=env,
            reference_id=item.reference_id,
            status=status,
            attempt_count=attempts,
            error_code=code,
            error_message=_public_error_message(code),
        )
        return status is not SkillCenterReferenceStatus.FAILED

    def _fail(self, env: str, reference_id: str, code: str) -> None:
        self._references.update_item(
            env=env,
            reference_id=reference_id,
            status=SkillCenterReferenceStatus.FAILED,
            error_code=code,
            error_message=_public_error_message(code),
        )


class _PermanentReferenceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _positive_int(value: object, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise _PermanentReferenceError(
            "SC_SKILL_NOT_FOUND", f"Skill Center {field} is not an integer"
        ) from exc
    if parsed < 1:
        raise _PermanentReferenceError(
            "SC_SKILL_NOT_FOUND", f"Skill Center {field} must be positive"
        )
    return parsed


def _final_add_error_code(error: Exception | None) -> str:
    if error is None:
        return "SKILL_SET_UPDATE_FAILED"
    if isinstance(error, SkillOfflineError):
        return "SKILL_OFFLINE"
    if isinstance(error, SkillSetControlPlaneNotFoundError):
        return "SKILL_SET_NOT_FOUND"
    if isinstance(error, SkillSetRuntimeReconcileError):
        return "RUNTIME_PROJECTION_FAILED"
    if isinstance(error, SkillSetAccessDeniedError):
        return "SKILL_SET_FORBIDDEN"
    return "SKILL_SET_UPDATE_FAILED"


def _public_error_message(code: str) -> str:
    return _PUBLIC_REFERENCE_ERRORS.get(code, "Reference operation failed")


__all__ = ["SkillCenterReferenceProcessor"]


class SkillCenterReferenceTaskHandler:
    def __init__(self, processor: SkillCenterReferenceProcessor) -> None:
        self._processor = processor

    @property
    def task_type(self) -> str:
        from agentclaw.community.core.skill_center.services.skill_center_reference_service import (
            SKILL_CENTER_REFERENCE_TASK,
        )

        return SKILL_CENTER_REFERENCE_TASK

    def handle(self, payload: dict) -> TaskOutcome:
        request_id = payload.get("request_id")
        if (
            not isinstance(request_id, str)
            or not request_id.strip()
            or request_id != request_id.strip()
        ):
            return Fail("request_id must be a non-empty unpadded string")
        return asyncio.run(self._processor.process(request_id))


__all__ = ["SkillCenterReferenceProcessor", "SkillCenterReferenceTaskHandler"]
