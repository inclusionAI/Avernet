"""Service API contract for the Todo Product Progress projection."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.progress_models import TodoProgress


@runtime_checkable
class TodoProductProgressServiceProtocol(Protocol):
    """Project product runtime facts without exposing protocol DTOs to callers."""

    @abstractmethod
    async def get_progress(
        self,
        *,
        todo_id: str,
        actor_id: str,
        expanded_stage_code: str | None = None,
        limit: int = 50,
    ) -> TodoProgress: ...
