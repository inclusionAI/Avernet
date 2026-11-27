"""Service API contract for product-level Todo creation."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.todos.models import TodoRecord
    from agentclaw.community.core.task.todos.product_creation_models import (
        EnsureProductColletResult,
        TodoDimaImportItem,
        TodoDimaImportResult,
        TodoExecutorSelection,
        TodoOwnerInput,
    )
    from agentclaw.community.core.task.todos.source_models import TodoSourceInput


@runtime_checkable
class TodoProductCreationServiceProtocol(Protocol):
    """Create a Todo from one product executor without exposing Template names."""

    @abstractmethod
    def create_todo(
        self,
        *,
        space_id: int,
        actor_id: str,
        title: str,
        description: str,
        executor: TodoExecutorSelection,
        owner: TodoOwnerInput,
        source: TodoSourceInput | None = None,
        env: str | None = None,
    ) -> TodoRecord: ...

    @abstractmethod
    async def import_dima_todos(
        self,
        *,
        space_id: int,
        actor_id: str,
        executor: TodoExecutorSelection,
        owner: TodoOwnerInput,
        items: tuple[TodoDimaImportItem, ...],
        env: str | None = None,
    ) -> tuple[TodoDimaImportResult, ...]: ...

    @abstractmethod
    async def ensure_product_collet(
        self,
        *,
        todo_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> EnsureProductColletResult: ...
