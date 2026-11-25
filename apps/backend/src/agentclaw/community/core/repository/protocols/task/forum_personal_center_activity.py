"""Repository contract for server-paged personal-center activities."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.forum.interaction.models import (
        ForumActorRef,
        PersonalCenterActorScope,
        PersonalCenterActivityPage,
        PersonalCenterActivityType,
        ForumInteractionTargetType,
    )


@runtime_checkable
class ForumPersonalCenterActivityRepositoryProtocol(Protocol):
    @abstractmethod
    def list_activities(
        self,
        *,
        actors: tuple[ForumActorRef, ...],
        actor_scope: PersonalCenterActorScope,
        activity_type: PersonalCenterActivityType,
        target_type: ForumInteractionTargetType | None,
        page: int,
        page_size: int,
    ) -> PersonalCenterActivityPage:
        """Read one unified server-paged activity result."""
