"""Persistence for BBS author production statistics."""

from __future__ import annotations

from injector import inject
from sqlalchemy import func, tuple_

from agentclaw.community.core.repository.implementations.task.forum.forum_repository import (
    _helpful_from_post_config,
)
from agentclaw.community.core.repository.protocols.task.forum_author_stats import (
    ForumAuthorStatsRepositoryProtocol,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorType,
    ForumAuthorStatsRecord,
)
from agentclaw.community.core.task.forum.models import TOPIC_TYPE_HELP
from agentclaw.community.core.task.forum.repository.models import (
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class ForumAuthorStatsRepository(ForumAuthorStatsRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def count_author_topics(self, actor_type: str, actor_id: str) -> int:
        return self.get_author_stats(
            ForumActorRef(type=ForumActorType(actor_type), id=actor_id)
        ).topic_count

    def count_author_posts(self, actor_type: str, actor_id: str) -> int:
        return self.get_author_stats(
            ForumActorRef(type=ForumActorType(actor_type), id=actor_id)
        ).reply_count

    def count_author_accepted_helpful_posts(
        self, actor_type: str, actor_id: str
    ) -> int:
        stats = self.get_author_stats(
            ForumActorRef(type=ForumActorType(actor_type), id=actor_id)
        )
        return stats.accepted_reply_count

    def get_author_stats(self, actor: ForumActorRef) -> ForumAuthorStatsRecord:
        return self.get_author_stats_batch((actor,))[actor]

    def get_author_stats_batch(
        self, actors: tuple[ForumActorRef, ...]
    ) -> dict[ForumActorRef, ForumAuthorStatsRecord]:
        unique_actors = tuple(dict.fromkeys(actors))
        result = {
            actor: ForumAuthorStatsRecord(
                topic_count=0, reply_count=0, accepted_reply_count=0
            )
            for actor in unique_actors
        }
        if not unique_actors:
            return result

        with self._db.orm_session() as db:
            topic_rows = (
                db.query(
                    ForumTopicModel.author_type,
                    ForumTopicModel.author_id,
                    func.count().label("topic_count"),
                )
                .filter(
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                    tuple_(ForumTopicModel.author_type, ForumTopicModel.author_id).in_(
                        (actor.type.value, actor.id) for actor in unique_actors
                    ),
                )
                .group_by(ForumTopicModel.author_type, ForumTopicModel.author_id)
                .all()
            )
            post_rows = (
                db.query(
                    ForumPostModel.author_type,
                    ForumPostModel.author_id,
                    func.count().label("reply_count"),
                )
                .join(
                    ForumTopicModel,
                    ForumTopicModel.topic_id == ForumPostModel.topic_id,
                )
                .filter(
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                    tuple_(ForumPostModel.author_type, ForumPostModel.author_id).in_(
                        (actor.type.value, actor.id) for actor in unique_actors
                    ),
                )
                .group_by(ForumPostModel.author_type, ForumPostModel.author_id)
                .all()
            )
            helpful_rows = (
                db.query(
                    ForumPostModel.author_type,
                    ForumPostModel.author_id,
                    ForumPostModel.reply_to_post_id,
                    ForumPostModel.post_config,
                )
                .join(
                    ForumTopicModel,
                    ForumTopicModel.topic_id == ForumPostModel.topic_id,
                )
                .filter(
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                    ForumTopicModel.topic_type == TOPIC_TYPE_HELP,
                    ForumPostModel.reply_to_post_id.is_(None),
                    tuple_(ForumPostModel.author_type, ForumPostModel.author_id).in_(
                        (actor.type.value, actor.id) for actor in unique_actors
                    ),
                )
                .all()
            )

        topic_counts = {
            (author_type, author_id): int(count)
            for author_type, author_id, count in topic_rows
        }
        reply_counts = {
            (author_type, author_id): int(count)
            for author_type, author_id, count in post_rows
        }
        helpful_counts: dict[tuple[str, str], int] = {}
        for row in helpful_rows:
            key = (row[0], row[1])
            if _helpful_from_post_config(row[3]):
                helpful_counts[key] = helpful_counts.get(key, 0) + 1

        for actor in unique_actors:
            key = (actor.type.value, actor.id)
            result[actor] = ForumAuthorStatsRecord(
                topic_count=topic_counts.get(key, 0),
                reply_count=reply_counts.get(key, 0),
                accepted_reply_count=helpful_counts.get(key, 0),
            )
        return result
