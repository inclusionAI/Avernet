"""Persistence contract for A-owned Todo product records."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.todos.models import (
        AcceptanceVerdict,
        TodoPage,
        TodoRecord,
        TodoStatus,
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
    def list_todos(
        self,
        *,
        space_id: int,
        keyword: str | None,
        status: TodoStatus | None,
        bot_id: str | None,
        page: int,
        page_size: int,
    ) -> TodoPage:
        """Return a database-filtered page of persisted product Todos."""
        ...

    @abstractmethod
    def list_todos_by_status(
        self,
        *,
        status: TodoStatus,
        limit: int,
    ) -> tuple[TodoRecord, ...]:
        """Return the oldest persisted Todos in one state for scheduler work."""
        ...

    @abstractmethod
    def bind_context_if_pending(
        self, *, todo_id: str, collab_context_id: str
    ) -> TodoRecord:
        """Attach a B Context only while the Todo remains pending."""
        ...

    @abstractmethod
    def transition_status(
        self,
        *,
        todo_id: str,
        expected_status: TodoStatus,
        new_status: TodoStatus,
    ) -> TodoRecord:
        """Compare and set the persisted product state under a row lock."""
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
