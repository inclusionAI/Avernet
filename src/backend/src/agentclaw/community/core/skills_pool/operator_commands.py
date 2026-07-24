"""Operator-triggered reconciliation and recovery commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from injector import inject

from agentclaw.community.core.skills_pool.reconcile_task import (
    SKILLS_POOL_RECONCILE_DEADLINE_SECONDS,
    SKILLS_POOL_RECONCILE_TASK,
    build_skills_pool_reconcile_payload,
)
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)


class OperatorCommandOutcome(StrEnum):
    ENQUEUED = "enqueued"
    NOT_CLAIMED = "not_claimed"
    NOT_RETRYABLE = "not_retryable"


@dataclass(frozen=True, slots=True)
class OperatorCommandResult:
    outcome: OperatorCommandOutcome
    wakeup_id: str | None = None


class SkillsPoolOperatorCommands:
    """Durably wake one Bot without mutating its migration facts."""

    @inject
    def __init__(
        self,
        *,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        task_queue_service: TaskQueueService,
    ) -> None:
        self._layouts = layout_repository
        self._queue = task_queue_service

    def wake(
        self,
        *,
        scope: BotSkillLayoutScope,
        operator: str,
        retry_only: bool = False,
    ) -> OperatorCommandResult:
        if not operator.strip():
            raise ValueError("operator is required")
        state = self._layouts.get(scope)
        if retry_only and not state.persisted:
            return OperatorCommandResult(OperatorCommandOutcome.NOT_CLAIMED)
        if retry_only and state.last_failure_retryable is not True:
            return OperatorCommandResult(OperatorCommandOutcome.NOT_RETRYABLE)
        wakeup_id = uuid4().hex
        self._queue.enqueue(
            SKILLS_POOL_RECONCILE_TASK,
            build_skills_pool_reconcile_payload(
                scope=scope,
                source="operator_retry" if retry_only else "operator_wakeup",
                signal_identity={"operator": operator.strip()},
                wakeup_id=wakeup_id,
            ),
            deadline_seconds=SKILLS_POOL_RECONCILE_DEADLINE_SECONDS,
        )
        return OperatorCommandResult(
            OperatorCommandOutcome.ENQUEUED,
            wakeup_id=wakeup_id,
        )
