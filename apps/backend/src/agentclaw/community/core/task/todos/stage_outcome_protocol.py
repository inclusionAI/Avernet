"""Service API for advancing a Todo's current stage."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.task.todos.models import TodoRecord
@runtime_checkable
class TodoStageServiceProtocol(Protocol):
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
