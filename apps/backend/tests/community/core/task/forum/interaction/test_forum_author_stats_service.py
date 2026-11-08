"""Domain tests for the BBS author production-statistics service."""

from __future__ import annotations

from agentclaw.community.core.repository.protocols.task.forum_author_stats import (
    ForumAuthorStatsRepositoryProtocol,
)
from agentclaw.community.core.task.forum.interaction.forum_author_stats_service import (
    ForumAuthorStatsService,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorType,
    ForumAuthorStatsRecord,
)


class FakeAuthorStatsRepository(ForumAuthorStatsRepositoryProtocol):
    def __init__(self) -> None:
        self.single_actors: list[ForumActorRef] = []
        self.batch_actors: list[tuple[ForumActorRef, ...]] = []

    def count_author_topics(self, _actor_type: str, _actor_id: str) -> int:
        raise AssertionError("usage counters must flow through the same aggregate")

    def count_author_posts(self, _actor_type: str, _actor_id: str) -> int:
        raise AssertionError("usage counters must flow through the same aggregate")

    def count_author_accepted_helpful_posts(
        self, _actor_type: str, _actor_id: str
    ) -> int:
        raise AssertionError("usage counters must flow through the same aggregate")

    def get_author_stats(self, actor: ForumActorRef) -> ForumAuthorStatsRecord:
        self.single_actors.append(actor)
        return ForumAuthorStatsRecord(
            topic_count=1, reply_count=2, accepted_reply_count=3
        )

    def get_author_stats_batch(
        self, actors: tuple[ForumActorRef, ...]
    ) -> dict[ForumActorRef, ForumAuthorStatsRecord]:
        self.batch_actors.append(actors)
        return {
            actor: ForumAuthorStatsRecord(
                topic_count=1, reply_count=2, accepted_reply_count=3
            )
            for actor in actors
        }


def test_get_author_stats_delegates_the_stable_actor() -> None:
    repository = FakeAuthorStatsRepository()
    actor = ForumActorRef(type=ForumActorType.HUMAN, id="user-a")
    service = ForumAuthorStatsService(repository)

    result = service.get_author_stats(actor)

    assert repository.single_actors == [actor]
    assert result == ForumAuthorStatsRecord(
        topic_count=1, reply_count=2, accepted_reply_count=3
    )


def test_get_author_stats_batch_preserves_the_requested_batch() -> None:
    repository = FakeAuthorStatsRepository()
    actors = (
        ForumActorRef(type=ForumActorType.HUMAN, id="user-a"),
        ForumActorRef(type=ForumActorType.BOT, id="bot-a"),
    )
    service = ForumAuthorStatsService(repository)

    result = service.get_author_stats_batch(actors)

    assert repository.batch_actors == [actors]
    assert result == {
        actors[0]: ForumAuthorStatsRecord(
            topic_count=1, reply_count=2, accepted_reply_count=3
        ),
        actors[1]: ForumAuthorStatsRecord(
            topic_count=1, reply_count=2, accepted_reply_count=3
        ),
    }
