"""Transport-agnostic Service API contracts for BBS interactions."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumAuthorStatsRecord,
    ForumInteractionRecord,
    ForumInteractionSummaryQuery,
    ForumInteractionSummaryResult,
)


@runtime_checkable
class ForumInteractionServiceProtocol(Protocol):
    @abstractmethod
    def record_interaction(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action_type: str,
        target_type: str,
        target_id: str,
    ) -> ForumInteractionRecord:
        """Validate and append exactly one interaction event.

        Repeated valid requests append further valid events; this protocol has
        no request idempotency key.
        """

    @abstractmethod
    def get_summary(
        self, query: ForumInteractionSummaryQuery
    ) -> ForumInteractionSummaryResult:
        """Read one actor-only, target-only, or actor-target summary."""

    @abstractmethod
    def get_summary_batch(
        self, items: tuple[ForumInteractionSummaryQuery, ...]
    ) -> tuple[ForumInteractionSummaryResult, ...]:
        """Read a whole validated batch and preserve the request order."""


@runtime_checkable
class ForumAuthorStatsServiceProtocol(Protocol):
    @abstractmethod
    def get_author_stats(self, actor: ForumActorRef) -> ForumAuthorStatsRecord:
        """Read production counts for one author."""

    @abstractmethod
    def get_author_stats_batch(
        self, actors: tuple[ForumActorRef, ...]
    ) -> dict[ForumActorRef, ForumAuthorStatsRecord]:
        """Read production counts for a batch of authors in request order."""
