"""Operator-directed recovery for a fenced Skills Pool migration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skills_pool.models import (
    FILESYSTEM_POOL_ENGINES,
    PoolCutoverStatus,
    PoolSkillMapping,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.aicoding_retirement import (
    is_trusted_aicoding_repo_restoration_resume,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    build_logical_skill_mappings,
    local_locators_from_evidence,
    local_skill_name,
)
from agentclaw.community.core.skills_pool.edit_guard import SkillsPoolEditGuard
from agentclaw.community.core.skills_pool.ports import SkillsPoolRuntimeProtocol
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolSkillRepositoryProtocol
from agentclaw.community.core.skills_pool.reconcile_task import (
    SKILLS_POOL_RECONCILE_DEADLINE_SECONDS,
    SKILLS_POOL_RECONCILE_TASK,
    build_skills_pool_reconcile_payload,
)
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolLayoutRepositoryProtocol
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayoutPhase,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class ManualRepairResolution(StrEnum):
    """The filesystem fact an operator proved on the current runtime."""

    POOL_COMMITTED = "pool_committed"
    LEGACY_NOT_COMMITTED = "legacy_not_committed"


class SkillsPoolRecoveryOutcome(StrEnum):
    RETRIGGERED = "retriggered"
    RETRIGGER_FAILED = "retrigger_failed"
    NOT_FOUND = "not_found"
    NOT_REPAIR_REQUIRED = "not_repair_required"
    STALE_GENERATION = "stale_generation"
    INVALID_REQUEST = "invalid_request"
    STATE_RACE_LOST = "state_race_lost"


@dataclass(frozen=True, slots=True)
class SkillsPoolRecoveryResult:
    outcome: SkillsPoolRecoveryOutcome


class SkillsPoolRecoveryService:
    """Record an operator finding, then durably resume the same generation."""

    @inject
    def __init__(
        self,
        *,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        task_queue_service: TaskQueueService,
    ) -> None:
        self._layouts = layout_repository
        self._queue = task_queue_service

    def resolve_repair_state(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        operator: str,
        note: str,
        resolution: ManualRepairResolution,
    ) -> SkillsPoolRecoveryResult:
        if not operator.strip() or not note.strip():
            return SkillsPoolRecoveryResult(SkillsPoolRecoveryOutcome.INVALID_REQUEST)
        state = self._layouts.get(scope)
        if not state.persisted:
            return SkillsPoolRecoveryResult(SkillsPoolRecoveryOutcome.NOT_FOUND)
        retrying_resolved_enqueue = (
            state.migration_generation == migration_generation
            and state.last_failure_code == "MANUAL_REPAIR_RESOLVED"
            and state.phase
            in {
                SkillLayoutPhase.POOL_READY,
                SkillLayoutPhase.POOL_CUTOVER_COMMITTED,
            }
        )
        if (
            state.phase is not SkillLayoutPhase.NEEDS_MANUAL_REPAIR
            and not retrying_resolved_enqueue
        ):
            return SkillsPoolRecoveryResult(
                SkillsPoolRecoveryOutcome.NOT_REPAIR_REQUIRED
            )
        if state.migration_generation != migration_generation:
            return SkillsPoolRecoveryResult(SkillsPoolRecoveryOutcome.STALE_GENERATION)

        if not retrying_resolved_enqueue:
            committed = resolution is ManualRepairResolution.POOL_COMMITTED
            if not self._layouts.resolve_repair(
                scope=scope,
                migration_generation=migration_generation,
                operator=operator.strip(),
                note=note.strip(),
                cutover_committed=committed,
            ):
                return SkillsPoolRecoveryResult(
                    SkillsPoolRecoveryOutcome.STATE_RACE_LOST
                )

        payload = build_skills_pool_reconcile_payload(
            scope=scope,
            source="manual_repair_resolution",
            signal_identity={
                "operator": operator.strip(),
                "resolution": resolution.value,
            },
        )
        try:
            self._queue.enqueue(
                SKILLS_POOL_RECONCILE_TASK,
                payload,
                deadline_seconds=SKILLS_POOL_RECONCILE_DEADLINE_SECONDS,
            )
        except Exception:
            logger.exception(
                "[skills_pool.recovery] durable retrigger enqueue failed "
                "env=%s entity_id=%s bot_id=%s generation=%s",
                scope.env,
                scope.entity_id,
                scope.bot_id,
                migration_generation,
            )
            return SkillsPoolRecoveryResult(SkillsPoolRecoveryOutcome.RETRIGGER_FAILED)
        return SkillsPoolRecoveryResult(SkillsPoolRecoveryOutcome.RETRIGGERED)


class SkillsPoolRollbackOutcome(StrEnum):
    LEGACY_ACTIVE = "legacy_active"
    NOT_FOUND = "not_found"
    NOT_POOL_ACTIVE = "not_pool_active"
    SERVICE_BOT_UNSUPPORTED = "service_bot_unsupported"
    STALE_GENERATION = "stale_generation"
    INVALID_REQUEST = "invalid_request"
    EDIT_BUSY = "edit_busy"
    BOT_CHANGED = "bot_changed"
    STATE_RACE_LOST = "state_race_lost"
    ROLLBACK_FAILED = "rollback_failed"
    MAPPING_FAILED = "mapping_failed"
    MAPPING_VERIFY_FAILED = "mapping_verify_failed"
    DATABASE_COMMIT_FAILED = "database_commit_failed"


@dataclass(frozen=True, slots=True)
class SkillsPoolRollbackResult:
    outcome: SkillsPoolRollbackOutcome
    evidence: dict[str, object] | None = None
    retryable: bool | None = None


class SkillsPoolRollbackService:
    """显式地从当前 Pool 内容重建 Legacy，并只向 Legacy 收敛。"""

    _LEASE_SECONDS = 300

    @inject
    def __init__(
        self,
        *,
        bot_repository: BotRepository,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        skill_repository: SkillsPoolSkillRepositoryProtocol,
        runtime: SkillsPoolRuntimeProtocol,
        edit_guard: SkillsPoolEditGuard,
    ) -> None:
        self._bots = bot_repository
        self._layouts = layout_repository
        self._skills = skill_repository
        self._runtime = runtime
        self._edit_guard = edit_guard

    async def rollback(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        operator: str,
        note: str,
    ) -> SkillsPoolRollbackResult:
        # Service Drafts intentionally have no Pool -> Legacy operator rollback.
        # Their image policy is coupled to the Pool-only runtime contract; the
        # generic filesystem rollback cannot safely produce a deployable
        # service artifact without also coordinating Runtime Pin. Keep that
        # cross-domain recovery path closed until it has an explicit contract.
        bot = self._bots.get_by_id_and_entity(scope.bot_id, scope.entity_id)
        if bot is not None and bot.get("bot_type") == "service":
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.SERVICE_BOT_UNSUPPORTED,
                evidence={"reason": "service_draft_pool_rollback_disabled"},
                retryable=False,
            )

        edit_lease = self._edit_guard.acquire_for_rollback(scope=scope)
        if edit_lease is None:
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.EDIT_BUSY,
                evidence={"reason": "local_skill_edit_in_progress"},
                retryable=True,
            )
        try:
            return await self._rollback_with_edit_pause(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                operator=operator,
                note=note,
            )
        finally:
            self._edit_guard.release(edit_lease)

    async def _rollback_with_edit_pause(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        operator: str,
        note: str,
    ) -> SkillsPoolRollbackResult:
        if not all(
            value.strip()
            for value in (rollback_generation, lease_owner, operator, note)
        ):
            return SkillsPoolRollbackResult(SkillsPoolRollbackOutcome.INVALID_REQUEST)

        state = self._layouts.get(scope)
        began_rollback = False
        if not state.persisted:
            return SkillsPoolRollbackResult(SkillsPoolRollbackOutcome.NOT_FOUND)
        if state.phase is SkillLayoutPhase.POOL_ACTIVE:
            if not self._layouts.begin_legacy_rollback(
                scope=scope,
                rollback_generation=rollback_generation,
                operator=operator.strip(),
                note=note.strip(),
                lease_owner=lease_owner,
                lease_seconds=self._LEASE_SECONDS,
            ):
                return SkillsPoolRollbackResult(
                    SkillsPoolRollbackOutcome.STATE_RACE_LOST
                )
            began_rollback = True
            state = self._layouts.get(scope)
        elif state.phase not in {
            SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING,
            SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED,
        }:
            return SkillsPoolRollbackResult(SkillsPoolRollbackOutcome.NOT_POOL_ACTIVE)

        if state.migration_generation != rollback_generation:
            return SkillsPoolRollbackResult(SkillsPoolRollbackOutcome.STALE_GENERATION)
        if not began_rollback and not self._layouts.try_acquire_rollback_lease(
            scope=scope,
            rollback_generation=rollback_generation,
            lease_owner=lease_owner,
            lease_seconds=self._LEASE_SECONDS,
        ):
            return SkillsPoolRollbackResult(SkillsPoolRollbackOutcome.STATE_RACE_LOST)

        bot = self._bots.get_by_id_and_entity(scope.bot_id, scope.entity_id)
        if bot is None or bot.get("env") != scope.env:
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.BOT_CHANGED,
                code="ROLLBACK_BOT_CHANGED",
                stage="rollback_bot_validation",
                retryable=False,
                evidence={"reason": "bot_missing_or_environment_changed"},
            )
        engine = bot.get("active_engine")
        owner_id = bot.get("owner_id")
        if (
            not isinstance(engine, str)
            or not isinstance(owner_id, (str, int))
            or isinstance(owner_id, bool)
        ):
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.BOT_CHANGED,
                code="ROLLBACK_BOT_CHANGED",
                stage="rollback_bot_validation",
                retryable=False,
                evidence={"reason": "bot_engine_or_owner_invalid"},
            )
        if engine not in FILESYSTEM_POOL_ENGINES:
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.BOT_CHANGED,
                code="ROLLBACK_ENGINE_UNSUPPORTED",
                stage="rollback_bot_validation",
                retryable=False,
                evidence={"reason": f"engine Pool layout not implemented: {engine}"},
            )
        user_id = str(owner_id)

        probe = await self._runtime.probe(
            bot_id=scope.bot_id,
            user_id=user_id,
            engine=engine,
        )
        restoration_resume = is_trusted_aicoding_repo_restoration_resume(
            state=state,
            engine=engine,
            probe=probe,
        )
        if (
            probe.status is not RuntimeLayoutProbeStatus.READY
            and not restoration_resume
        ):
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.ROLLBACK_FAILED,
                code=f"ROLLBACK_RUNTIME_{probe.status.value}",
                stage="runtime_probe",
                retryable=probe.status
                in {
                    RuntimeLayoutProbeStatus.NOT_CAPABLE,
                    RuntimeLayoutProbeStatus.TRANSIENT_ERROR,
                },
                evidence=probe.evidence,
            )

        local_assets = self._skills.list_bot_local_assets(
            env=scope.env,
            bot_id=scope.bot_id,
        )
        active_assets = self._skills.list_bot_active_assets(
            env=scope.env,
            bot_id=scope.bot_id,
            user_id=user_id,
            engine=engine,
        )
        try:
            local_names = [local_skill_name(asset) for asset in local_assets]
            mappings = build_logical_skill_mappings(active_assets)
        except ValueError as error:
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.ROLLBACK_FAILED,
                code="ROLLBACK_DATA_INCONSISTENT",
                stage="rollback_validation",
                retryable=False,
                evidence={"reason": str(error)},
            )

        locator_evidence = self._persisted_rollback_evidence(state.last_probe_evidence)
        if state.phase is SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING:
            # Pool cutover retires Legacy local storage, so Legacy mappings
            # cannot be published until the runtime has rebuilt that corpus.
            cutover = await self._runtime.rollback_to_legacy(
                bot_id=scope.bot_id,
                user_id=user_id,
                rollback_generation=rollback_generation,
                registered_local_names=local_names,
            )
            if not cutover.committed:
                return self._failure(
                    scope=scope,
                    rollback_generation=rollback_generation,
                    lease_owner=lease_owner,
                    outcome=SkillsPoolRollbackOutcome.ROLLBACK_FAILED,
                    code=f"ROLLBACK_{cutover.status.value}",
                    stage="filesystem_rollback",
                    # Runtime rollback is idempotent: after a lost response the
                    # same generation can prove ALREADY_COMMITTED, so UNKNOWN
                    # is safe to retry rather than guess filesystem truth.
                    retryable=cutover.status
                    in {
                        PoolCutoverStatus.POST_CUTOVER_SYNC_PENDING,
                        PoolCutoverStatus.TRANSIENT_ERROR,
                        PoolCutoverStatus.UNKNOWN,
                    },
                    evidence=cutover.to_dict(),
                )
            if not self._layouts.record_legacy_rollback_committed(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                evidence=cutover.to_dict(),
            ):
                return SkillsPoolRollbackResult(
                    SkillsPoolRollbackOutcome.STATE_RACE_LOST
                )
            locator_evidence = cutover.evidence

        # Re-publish after the atomic exchange as an idempotent confirmation.
        # A process that resumes from LEGACY_ROLLBACK_COMMITTED starts here.
        post_cutover_mapping = await self._publish_and_verify_mappings(
            scope=scope,
            user_id=user_id,
            mappings=mappings,
            rollback_generation=rollback_generation,
            lease_owner=lease_owner,
        )
        if post_cutover_mapping is not None:
            return post_cutover_mapping

        try:
            local_locators = local_locators_from_evidence(
                local_assets,
                local_names,
                locator_evidence,
            )
        except ValueError as error:
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.DATABASE_COMMIT_FAILED,
                code="ROLLBACK_LOCATOR_EVIDENCE_INVALID",
                stage="control_plane_commit",
                retryable=True,
                evidence={"reason": str(error)},
            )
        if not self._layouts.commit_legacy_active(
            scope=scope,
            rollback_generation=rollback_generation,
            lease_owner=lease_owner,
            local_locators=local_locators,
        ):
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.DATABASE_COMMIT_FAILED,
                code="ROLLBACK_DATABASE_COMMIT_FAILED",
                stage="control_plane_commit",
                retryable=True,
                evidence={"local_locator_count": len(local_locators)},
            )
        return SkillsPoolRollbackResult(SkillsPoolRollbackOutcome.LEGACY_ACTIVE)

    async def _publish_and_verify_mappings(
        self,
        *,
        scope: BotSkillLayoutScope,
        user_id: str,
        mappings: list[PoolSkillMapping],
        rollback_generation: str,
        lease_owner: str,
    ) -> SkillsPoolRollbackResult | None:
        if not await self._runtime.publish_mappings(
            bot_id=scope.bot_id,
            user_id=user_id,
            mappings=mappings,
            retired_mappings=[],
            source_layout=SkillMappingSourceLayout.LEGACY,
        ):
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.MAPPING_FAILED,
                code="ROLLBACK_MAPPING_PUBLISH_FAILED",
                stage="mapping_publish",
                retryable=True,
                evidence={"mapping_count": len(mappings)},
            )
        if not await self._runtime.verify_mappings(
            bot_id=scope.bot_id,
            user_id=user_id,
            mappings=mappings,
            retired_mappings=[],
            source_layout=SkillMappingSourceLayout.LEGACY,
        ):
            return self._failure(
                scope=scope,
                rollback_generation=rollback_generation,
                lease_owner=lease_owner,
                outcome=SkillsPoolRollbackOutcome.MAPPING_VERIFY_FAILED,
                code="ROLLBACK_MAPPING_VERIFY_FAILED",
                stage="mapping_verify",
                retryable=True,
                evidence={"mapping_count": len(mappings)},
            )
        return None

    def _failure(
        self,
        *,
        scope: BotSkillLayoutScope,
        rollback_generation: str,
        lease_owner: str,
        outcome: SkillsPoolRollbackOutcome,
        code: str,
        stage: str,
        retryable: bool,
        evidence: dict[str, object],
    ) -> SkillsPoolRollbackResult:
        if not self._layouts.record_rollback_failure(
            scope=scope,
            rollback_generation=rollback_generation,
            lease_owner=lease_owner,
            failure_code=code,
            failure_stage=stage,
            retryable=retryable,
            evidence=evidence,
        ):
            return SkillsPoolRollbackResult(SkillsPoolRollbackOutcome.STATE_RACE_LOST)
        return SkillsPoolRollbackResult(
            outcome,
            evidence=evidence,
            retryable=retryable,
        )

    @staticmethod
    def _persisted_rollback_evidence(
        stored: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if not isinstance(stored, dict):
            return None
        rollback = stored.get("rollback")
        if not isinstance(rollback, dict):
            return None
        evidence = rollback.get("evidence")
        return evidence if isinstance(evidence, dict) else None


__all__ = [
    "ManualRepairResolution",
    "SkillsPoolRecoveryOutcome",
    "SkillsPoolRecoveryResult",
    "SkillsPoolRecoveryService",
    "SkillsPoolRollbackOutcome",
    "SkillsPoolRollbackResult",
    "SkillsPoolRollbackService",
]
