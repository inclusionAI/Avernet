"""Transport-agnostic Service API for the Todo workbench."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.models import (
    AcceptanceVerdict,
    TodoAcceptanceTarget,
    TodoEventRecord,
    TodoPage,
    TodoRecord,
    TodoStatus,
)


@runtime_checkable
class TodoServiceProtocol(Protocol):
    @abstractmethod
    def list_todos(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        status: TodoStatus | str | None,
        page: int,
        page_size: int,
        bot_id: str | None = None,
        env: str | None = None,
    ) -> TodoPage: ...

    @abstractmethod
    def get_todo(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> TodoRecord: ...

    @abstractmethod
    def create_todo(
        self,
        *,
        space_id: int,
        actor_id: str,
        title: str,
        description: str,
        bot_id: str,
        env: str | None = None,
    ) -> TodoRecord: ...

    @abstractmethod
    async def start_todo(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> TodoRecord: ...

    @abstractmethod
    def list_events(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> tuple[TodoEventRecord, ...]: ...

    @abstractmethod
    def list_interactions(
        self, *, todo_id: str, actor_id: str, env: str | None = None
    ) -> tuple[TodoEventRecord, ...]: ...

    @abstractmethod
    async def create_interaction(
        self,
        *,
        todo_id: str,
        actor_id: str,
        author_name: str | None,
        message: str,
        env: str | None = None,
    ) -> TodoEventRecord: ...

    @abstractmethod
    def submit_acceptance(
        self,
        *,
        todo_id: str,
        actor_id: str,
        verdict: AcceptanceVerdict | str,
        decision_id: str,
        message: str | None,
        targets: tuple[TodoAcceptanceTarget, ...] = (),
        env: str | None = None,
    ) -> TodoRecord: ...
