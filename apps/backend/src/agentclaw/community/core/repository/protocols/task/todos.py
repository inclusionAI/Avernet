"""Persistence contract for Todo product records."""

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
        offset: int,
        limit: int,
        bot_id: str | None = None,
    ) -> TodoPage: ...

    @abstractmethod
    def update_draft(
        self,
        *,
        todo_id: str,
        title: str,
        description: str,
        bot_id: str,
        bot_name: str,
    ) -> TodoRecord: ...

    @abstractmethod
    def start_if_draft(self, *, todo_id: str, collab_context_id: str) -> TodoRecord: ...

    @abstractmethod
    def mark_running_if_pending_acceptance(self, *, todo_id: str) -> TodoRecord: ...

    @abstractmethod
    def mark_pending_acceptance_if_in_progress(self, *, todo_id: str) -> TodoRecord: ...

    @abstractmethod
    def accept_if_pending(
        self,
        *,
        todo_id: str,
        verdict: AcceptanceVerdict,
        message: str,
        author_id: str,
    ) -> TodoRecord: ...
