"""Repository contract for BBS interaction state persistence and summaries."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.forum.interaction.models import (
        ForumActorRef,
        ForumActorTargetRef,
        ForumInteractionRecord,
        ForumStatsRecord,
        ForumTargetRef,
    )


@runtime_checkable
class ForumInteractionRepositoryProtocol(Protocol):
    @abstractmethod
    def insert_interaction(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action_type: str,
        target_type: str,
        target_id: str,
        active: Literal[1, -1],
    ) -> ForumInteractionRecord:
        """Apply a validated +1/-1 delta to the latest row for this action key."""

    @abstractmethod
    def get_summary(
        self,
        *,
        actor_type: str | None = None,
        actor_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> ForumStatsRecord:
        """Read one already-validated summary scope."""

    @abstractmethod
    def batch_get_actor_summaries(
        self, actor_refs: tuple[ForumActorRef, ...]
    ) -> dict[ForumActorRef, ForumStatsRecord]:
        """Read inbound interaction counts for a batch of actor identities."""

    @abstractmethod
    def batch_get_target_summaries(
        self, target_refs: tuple[ForumTargetRef, ...]
    ) -> dict[ForumTargetRef, ForumStatsRecord]:
        """Read public interaction counts and Topic projections for targets."""

    @abstractmethod
    def batch_get_actor_target_summaries(
        self, pairs: tuple[ForumActorTargetRef, ...]
    ) -> dict[ForumActorTargetRef, ForumStatsRecord]:
        """Read active action counts for exact actor-target pairs."""
