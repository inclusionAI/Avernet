"""Service API contract for Todo Product Runtime interactions."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.product_interaction_models import (
    TodoProductInteraction,
    TodoProductInteractionDeliveryState,
    TodoProductInteractionKind,
    TodoProductInteractionPage,
)


@runtime_checkable
class TodoProductInteractionServiceProtocol(Protocol):
    """Persist and send Human interactions without exposing protocol DTOs."""

    @abstractmethod
    async def list_interactions(
        self,
        *,
        todo_id: str,
        actor_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> TodoProductInteractionPage: ...

    @abstractmethod
    async def send_interaction(
        self,
        *,
        todo_id: str,
        actor_id: str,
        message: str,
        reply_to_interaction_id: str | None = None,
    ) -> TodoProductInteraction: ...

    @abstractmethod
    async def cancel_interaction(
        self,
        *,
        todo_id: str,
        actor_id: str,
        interaction_id: str,
    ) -> TodoProductInteraction: ...


__all__ = [
    "TodoProductInteractionDeliveryState",
    "TodoProductInteractionKind",
    "TodoProductInteractionServiceProtocol",
]
