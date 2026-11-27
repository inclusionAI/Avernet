"""Persistence contract for local Todo Product interaction facts."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.todos.product_interaction_models import (
        TodoProductInteraction,
        TodoProductInteractionKind,
    )


@runtime_checkable
class TodoProductInteractionRepositoryProtocol(Protocol):
    @abstractmethod
    def create_interaction_single_flight(
        self,
        *,
        todo_id: str,
        interaction_id: str,
        kind: TodoProductInteractionKind,
        actor_id: str,
        actor_name: str | None,
        message: str,
        product_collet_id: int,
        reply_to_interaction_id: str | None = None,
    ) -> TodoProductInteraction: ...

    @abstractmethod
    def request_cancel_and_create_stop(
        self,
        *,
        todo_id: str,
        interaction_id: str,
        stop_interaction_id: str,
        actor_id: str,
        actor_name: str | None,
        message: str,
        product_collet_id: int,
    ) -> TodoProductInteraction: ...

    @abstractmethod
    def get_interaction(
        self, *, todo_id: str, interaction_id: str
    ) -> TodoProductInteraction: ...

    @abstractmethod
    def list_interactions(
        self, *, todo_id: str, page: int, page_size: int
    ) -> tuple[TodoProductInteraction, ...]: ...

    @abstractmethod
    def get_blocking_interaction(self, *, todo_id: str) -> TodoProductInteraction | None: ...

    @abstractmethod
    def save_rollout_fence(
        self,
        *,
        interaction_id: str,
        collet_rollout_id: int,
        expected_revision: int,
    ) -> TodoProductInteraction: ...

    @abstractmethod
    def mark_delivery_unknown(
        self, *, interaction_id: str, failure_code: str
    ) -> TodoProductInteraction: ...

    @abstractmethod
    def complete_interaction(
        self,
        *,
        interaction_id: str,
        response_type: str,
        response_message: str,
    ) -> TodoProductInteraction: ...

    @abstractmethod
    def fail_interaction(
        self, *, interaction_id: str, failure_code: str
    ) -> TodoProductInteraction: ...

    @abstractmethod
    def mark_cancelled(
        self, *, todo_id: str, interaction_id: str
    ) -> TodoProductInteraction: ...


__all__ = ["TodoProductInteractionRepositoryProtocol"]
