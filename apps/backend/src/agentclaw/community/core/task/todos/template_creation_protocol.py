"""Service contract for expanding a published Todo template into a Todo."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.models import TodoRecord


@runtime_checkable
class TodoTemplateCreationServiceProtocol(Protocol):
    """Create a Todo and its resolved stage instances atomically."""

    @abstractmethod
    def create_todo_from_template(
        self,
        *,
        space_id: int,
        actor_id: str,
        template_id: str,
        template_version_id: str,
        title: str,
        description: str,
        bindings: Mapping[str, object],
        env: str | None = None,
    ) -> TodoRecord: ...
