"""Repository contract for BBS author production statistics."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.forum.interaction.models import (
        ForumActorRef,
        ForumAuthorStatsRecord,
    )


@runtime_checkable
class ForumAuthorStatsRepositoryProtocol(Protocol):
    @abstractmethod
    def count_author_topics(
        self, actor_type: str, actor_id: str
    ) -> int:
        """Count Topics authored by one identity in the current environment."""

    @abstractmethod
    def count_author_posts(self, actor_type: str, actor_id: str) -> int:
        """Count Posts authored by one identity in the current environment."""

    @abstractmethod
    def count_author_accepted_helpful_posts(
        self, actor_type: str, actor_id: str
    ) -> int:
        """Count currently-helpful first-level HELP replies by one identity."""

    @abstractmethod
    def get_author_stats(self, actor: ForumActorRef) -> ForumAuthorStatsRecord:
        """Read production counts for one author."""

    @abstractmethod
    def get_author_stats_batch(
        self, actors: tuple[ForumActorRef, ...]
    ) -> dict[ForumActorRef, ForumAuthorStatsRecord]:
        """Read production counts for all unique authors in one batch."""
