"""Execution application boundary for automatically scheduled Todos."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.models import TodoRecord


@runtime_checkable
class TodoExecutionServiceProtocol(Protocol):
    async def dispatch_pending(
        self,
        *,
        todo_id: str,
        env: str | None = None,
    ) -> TodoRecord: ...

    async def refresh_in_progress(self, *, todo_id: str) -> TodoRecord: ...

    async def resume_interaction(
        self,
        *,
        todo_id: str,
        actor_id: str,
        triggered_by_event_id: str,
    ) -> TodoRecord: ...


__all__ = ["TodoExecutionServiceProtocol"]
