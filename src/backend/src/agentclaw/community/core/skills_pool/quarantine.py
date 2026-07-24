"""Migration Quarantine retention, evidence and cleanup orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Callable, Protocol
from uuid import uuid4

from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.core.task_queue.types import (
    Complete,
    Reschedule,
    Retry,
    TaskOutcome,
)

QUARANTINE_RETENTION = timedelta(days=7)
SKILLS_POOL_QUARANTINE_CLEANUP_TASK = "skills_pool.quarantine.cleanup"
QUARANTINE_RECHECK_SECONDS = 24 * 60 * 60


class QuarantineStatus(StrEnum):
    RETAINED = "retained"
    CLEANING = "cleaning"
    CLEANED = "cleaned"
    CLEANUP_FAILED = "cleanup_failed"


class RuntimeReconciliationStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"


class RuntimeQuarantineCleanupStatus(StrEnum):
    CLEANED = "CLEANED"
    ALREADY_ABSENT = "ALREADY_ABSENT"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class RuntimeQuarantineCleanupResult:
    status: RuntimeQuarantineCleanupStatus
    evidence: dict[str, object]


class QuarantineBlocker(StrEnum):
    RETENTION_PERIOD = "retention_period"
    RUNTIME_EVIDENCE_MISSING = "runtime_evidence_missing"
    RUNTIME_RECONCILIATION_FAILED = "runtime_reconciliation_failed"
    LAYOUT_UNHEALTHY = "layout_unhealthy"
    ALREADY_CLEANED = "already_cleaned"
    CLEANUP_IN_PROGRESS = "cleanup_in_progress"


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    scope: BotSkillLayoutScope
    migration_generation: str
    engine: str
    path: str
    status: QuarantineStatus
    created_at: datetime
    pool_activated_at: datetime
    source_evidence: dict[str, object]
    runtime_reconciled_at: datetime | None = None
    runtime_reconciliation_status: RuntimeReconciliationStatus | None = None
    runtime_evidence: dict[str, object] | None = None
    cleaned_at: datetime | None = None
    cleanup_evidence: dict[str, object] | None = None
    cleanup_lease_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class QuarantineEligibility:
    eligible: bool
    age: timedelta
    eligible_at: datetime
    blockers: tuple[QuarantineBlocker, ...]


@dataclass(frozen=True, slots=True)
class QuarantineCleanupResult:
    status: QuarantineStatus
    evidence: dict[str, object]


@dataclass(frozen=True, slots=True)
class QuarantineOperationalView:
    record: QuarantineRecord
    age: timedelta
    eligible: bool
    eligible_at: datetime
    blockers: tuple[QuarantineBlocker, ...]


class QuarantineRepositoryProtocol(Protocol):
    def get_quarantine(
        self,
        scope: BotSkillLayoutScope,
        migration_generation: str,
    ) -> QuarantineRecord | None: ...

    def mark_cleaned(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        evidence: dict[str, object],
    ) -> bool: ...

    def claim_cleanup(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        lease_seconds: int,
        eligible_before: datetime,
    ) -> bool: ...

    def mark_cleanup_failed(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        evidence: dict[str, object],
    ) -> bool: ...

    def record_cleanup_uncertain(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        cleanup_owner: str,
        evidence: dict[str, object],
    ) -> bool: ...


class LayoutReaderProtocol(Protocol):
    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState: ...


class QuarantineRuntimeProtocol(Protocol):
    async def cleanup_quarantine(
        self,
        *,
        bot_id: str,
        user_id: str,
        engine: str,
        migration_generation: str,
    ) -> RuntimeQuarantineCleanupResult: ...


class SkillsPoolQuarantineService:
    """Apply the seven-day safety policy before deleting one exact generation."""

    def __init__(
        self,
        *,
        quarantine_repository: QuarantineRepositoryProtocol,
        layout_repository: LayoutReaderProtocol,
        runtime: QuarantineRuntimeProtocol,
        now: Callable[[], datetime] | None = None,
        runtime_timeout_seconds: float = 240,
    ) -> None:
        self._records = quarantine_repository
        self._layouts = layout_repository
        self._runtime = runtime
        self._now = now or (lambda: datetime.now(UTC))
        self._runtime_timeout_seconds = runtime_timeout_seconds

    @staticmethod
    def evaluate(
        record: QuarantineRecord,
        layout: BotSkillLayoutState,
        *,
        now: datetime,
    ) -> QuarantineEligibility:
        eligible_at = record.pool_activated_at + QUARANTINE_RETENTION
        blockers: list[QuarantineBlocker] = []
        if record.status is QuarantineStatus.CLEANED:
            blockers.append(QuarantineBlocker.ALREADY_CLEANED)
        if (
            record.status is QuarantineStatus.CLEANING
            and record.cleanup_lease_expires_at is not None
            and record.cleanup_lease_expires_at > now
        ):
            blockers.append(QuarantineBlocker.CLEANUP_IN_PROGRESS)
        if now < eligible_at:
            blockers.append(QuarantineBlocker.RETENTION_PERIOD)
        if (
            record.runtime_reconciled_at is None
            or record.runtime_reconciled_at <= record.pool_activated_at
        ):
            blockers.append(QuarantineBlocker.RUNTIME_EVIDENCE_MISSING)
        elif (
            record.runtime_reconciliation_status
            is not RuntimeReconciliationStatus.READY
        ):
            blockers.append(QuarantineBlocker.RUNTIME_RECONCILIATION_FAILED)
        if (
            layout.active_layout is not SkillLayout.POOL
            or layout.phase is not SkillLayoutPhase.POOL_ACTIVE
            or layout.migration_generation != record.migration_generation
        ):
            blockers.append(QuarantineBlocker.LAYOUT_UNHEALTHY)
        return QuarantineEligibility(
            eligible=not blockers,
            age=max(timedelta(), now - record.created_at),
            eligible_at=eligible_at,
            blockers=tuple(blockers),
        )

    def cleanup(
        self,
        scope: BotSkillLayoutScope,
        migration_generation: str,
    ) -> QuarantineCleanupResult:
        record = self._records.get_quarantine(scope, migration_generation)
        if record is None:
            return QuarantineCleanupResult(
                status=QuarantineStatus.CLEANED,
                evidence={"reason": "record_absent"},
            )
        now = self._now()
        eligibility = self.evaluate(
            record,
            self._layouts.get(scope),
            now=now,
        )
        if not eligibility.eligible:
            return QuarantineCleanupResult(
                status=record.status,
                evidence={
                    "blockers": [item.value for item in eligibility.blockers],
                    "eligible_at": eligibility.eligible_at.isoformat(),
                },
            )
        cleanup_owner = f"quarantine:{uuid4().hex}"
        if not self._records.claim_cleanup(
            scope=scope,
            migration_generation=migration_generation,
            cleanup_owner=cleanup_owner,
            lease_seconds=60 * 60,
            eligible_before=now - QUARANTINE_RETENTION,
        ):
            return QuarantineCleanupResult(
                status=QuarantineStatus.RETAINED,
                evidence={"blockers": ["layout_or_cleanup_state_changed"]},
            )

        async def invoke_runtime() -> RuntimeQuarantineCleanupResult:
            return await asyncio.wait_for(
                self._runtime.cleanup_quarantine(
                    bot_id=scope.bot_id,
                    user_id=scope.entity_id,
                    engine=record.engine,
                    migration_generation=migration_generation,
                ),
                timeout=self._runtime_timeout_seconds,
            )

        cleanup_uncertain = False
        try:
            response = asyncio.run(invoke_runtime())
        except TimeoutError:
            cleanup_uncertain = True
            response = RuntimeQuarantineCleanupResult(
                status=RuntimeQuarantineCleanupStatus.TRANSIENT_ERROR,
                evidence={"reason": "runtime_cleanup_outcome_unknown"},
            )
        evidence = response.evidence
        if response.status not in {
            RuntimeQuarantineCleanupStatus.CLEANED,
            RuntimeQuarantineCleanupStatus.ALREADY_ABSENT,
        }:
            cleanup_uncertain = cleanup_uncertain or evidence.get("reason") in {
                "runtime_cleanup_outcome_unknown",
                "invalid_runtime_response",
            }
            if cleanup_uncertain:
                self._records.record_cleanup_uncertain(
                    scope=scope,
                    migration_generation=migration_generation,
                    cleanup_owner=cleanup_owner,
                    evidence=evidence,
                )
            else:
                self._records.mark_cleanup_failed(
                    scope=scope,
                    migration_generation=migration_generation,
                    cleanup_owner=cleanup_owner,
                    evidence=evidence,
                )
            return QuarantineCleanupResult(
                status=QuarantineStatus.CLEANUP_FAILED,
                evidence=evidence,
            )
        committed = self._records.mark_cleaned(
            scope=scope,
            migration_generation=migration_generation,
            cleanup_owner=cleanup_owner,
            evidence=evidence,
        )
        return QuarantineCleanupResult(
            status=(
                QuarantineStatus.CLEANED
                if committed
                else QuarantineStatus.CLEANUP_FAILED
            ),
            evidence=evidence,
        )

    def inspect(
        self,
        scope: BotSkillLayoutScope,
        migration_generation: str,
    ) -> QuarantineOperationalView | None:
        record = self._records.get_quarantine(scope, migration_generation)
        if record is None:
            return None
        decision = self.evaluate(
            record,
            self._layouts.get(scope),
            now=self._now(),
        )
        return QuarantineOperationalView(
            record=record,
            age=decision.age,
            eligible=decision.eligible,
            eligible_at=decision.eligible_at,
            blockers=decision.blockers,
        )


class SkillsPoolQuarantineCleanupTaskHandler:
    def __init__(self, service: SkillsPoolQuarantineService) -> None:
        self._service = service

    @property
    def task_type(self) -> str:
        return SKILLS_POOL_QUARANTINE_CLEANUP_TASK

    def handle(self, payload: dict | None) -> TaskOutcome:
        try:
            raw_scope = payload["scope"]  # type: ignore[index]
            scope = BotSkillLayoutScope(
                env=str(raw_scope["env"]),
                entity_id=str(raw_scope["entity_id"]),
                bot_id=str(raw_scope["bot_id"]),
            )
            generation = str(payload["migration_generation"])  # type: ignore[index]
        except (KeyError, TypeError):
            return Complete()
        result = self._service.cleanup(scope, generation)
        if result.status is QuarantineStatus.CLEANED:
            return Complete()
        if result.status is QuarantineStatus.CLEANUP_FAILED:
            return Retry("migration quarantine cleanup failed")
        return Reschedule(QUARANTINE_RECHECK_SECONDS)
