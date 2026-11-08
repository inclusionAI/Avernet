"""Application service for BBS author production statistics."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.task.forum_author_stats import (
    ForumAuthorStatsRepositoryProtocol,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumAuthorStatsRecord,
)
from agentclaw.community.core.task.forum.interaction.service_protocol import (
    ForumAuthorStatsServiceProtocol,
)


class ForumAuthorStatsService(ForumAuthorStatsServiceProtocol):
    """Expose repository author aggregates without introducing transport state."""

    @inject
    def __init__(self, repository: ForumAuthorStatsRepositoryProtocol) -> None:
        self._repository = repository

    def get_author_stats(self, actor: ForumActorRef) -> ForumAuthorStatsRecord:
        return self._repository.get_author_stats(actor)

    def get_author_stats_batch(
        self, actors: tuple[ForumActorRef, ...]
    ) -> dict[ForumActorRef, ForumAuthorStatsRecord]:
        return self._repository.get_author_stats_batch(actors)
