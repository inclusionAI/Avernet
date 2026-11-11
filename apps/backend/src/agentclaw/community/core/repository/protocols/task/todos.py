"""Persistence contract for A-owned Todo product records."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.todos.models import (
        AcceptanceVerdict,
        TodoRecord,
    )


@runtime_checkable
class TodoRepositoryProtocol(Protocol):
    @abstractmethod
    def create_todo(
        self,
        *,
        space_id: int,
        title: str,
        description: str,
        bot_id: str,
        bot_name: str,
        created_by: str,
    ) -> TodoRecord: ...

    @abstractmethod
    def get_todo(self, *, todo_id: str) -> TodoRecord: ...

    @abstractmethod
    def list_todo_candidates(
        self,
        *,
        space_id: int,
        keyword: str | None,
        bot_id: str | None = None,
    ) -> tuple[TodoRecord, ...]:
        """Return all A-side candidates before live B-status projection."""
        ...

    @abstractmethod
    def bind_context_if_draft(
        self, *, todo_id: str, collab_context_id: str
    ) -> TodoRecord:
        """Attach the newly created B Context without changing A status."""
        ...

    @abstractmethod
    def record_acceptance(
        self,
        *,
        todo_id: str,
        verdict: AcceptanceVerdict,
        message: str,
        author_id: str,
    ) -> TodoRecord:
        """Atomically store the Human decision and resulting A state."""
        ...
