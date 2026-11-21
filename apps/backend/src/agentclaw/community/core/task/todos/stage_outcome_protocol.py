"""Service API for advancing a Todo's current stage."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.task.todos.models import TodoRecord
from agentclaw.community.core.task.todos.stage_outcome_models import TodoStageOutcomeKind


@runtime_checkable
class TodoStageServiceProtocol(Protocol):
    def report_bot_outcome(
        self,
        *,
        todo_id: str,
        stage_instance_id: str,
        todo_execution_id: str,
        execution_token: str,
        outcome: TodoStageOutcomeKind | str,
        summary: str,
        blocker_reason: str | None = None,
        requested_action: str | None = None,
        idempotency_key: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> TodoRecord: ...

    def complete_human_stage(
        self,
        *,
        todo_id: str,
        stage_instance_id: str,
        actor_id: str,
        summary: str,
        idempotency_key: str,
        result: dict[str, Any] | None = None,
        env: str | None = None,
    ) -> TodoRecord: ...

    def resume(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> TodoRecord: ...

    def cancel(
        self,
        *,
        todo_id: str,
        actor_id: str,
        reason: str | None = None,
        env: str | None = None,
    ) -> TodoRecord: ...
