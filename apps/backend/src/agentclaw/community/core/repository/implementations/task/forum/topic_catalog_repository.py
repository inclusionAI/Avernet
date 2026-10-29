"""Topic catalogue reads and hot-list ranking."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import func, or_, tuple_, union_all

from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorType,
    ForumInteractionActionType,
    ForumInteractionTargetType,
)
from agentclaw.community.core.task.forum.models import (
    ForumTopicPage,
)
from agentclaw.community.core.task.forum.repository.models import (
    ForumInteractionModel,
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class TopicCatalogRepositoryMixin:
    """Provide the paginated Topic catalogue with hot Topics first."""

    def list_topics(
        self,
        *,
        keyword: str | None,
        status: str | None,
        offset: int,
        limit: int,
        topic_type: str | None = None,
        author_id: str | None = None,
        followed_authors: tuple[ForumActorRef, ...] | None = None,
    ) -> ForumTopicPage:
        with self._db.orm_session() as db:
            # The catalogue is not a simple SQL page. Hot Topics can be older
            # than every ordinary Topic in the current database view, so rank
            # candidates are all evaluated before pagination is applied.
            post_stats = (
                db.query(
                    ForumPostModel.topic_id.label("topic_id"),
                    func.count().label("reply_count"),
                    func.max(ForumPostModel.gmt_create).label("latest_post_at"),
                    func.count(
                        func.distinct(
                            func.concat(
                                ForumPostModel.author_type,
                                ":",
                                ForumPostModel.author_id,
                            )
                        )
                    ).label("unique_reply_users"),
                )
                .group_by(ForumPostModel.topic_id)
                .subquery()
            )

            favorite_stats = self._topic_interaction_stats(
                db, ForumInteractionActionType.FAVORITE
            )
            like_stats = self._topic_interaction_stats(
                db, ForumInteractionActionType.LIKE
            )

            reply_rows = db.query(
                ForumPostModel.topic_id.label("topic_id"),
                ForumPostModel.author_type.label("actor_type"),
                ForumPostModel.author_id.label("actor_id"),
            )
            favorite_rows = self._topic_actor_rows(
                db, ForumInteractionActionType.FAVORITE
            )
            like_rows = self._topic_actor_rows(db, ForumInteractionActionType.LIKE)
            actor_rows = union_all(reply_rows, favorite_rows, like_rows).subquery()
            interactor_stats = (
                db.query(
                    actor_rows.c.topic_id.label("topic_id"),
                    func.count(
                        func.distinct(
                            func.concat(
                                actor_rows.c.actor_type,
                                ":",
                                actor_rows.c.actor_id,
                            )
                        )
                    ).label("unique_interactors"),
                )
                .group_by(actor_rows.c.topic_id)
                .subquery()
            )

            query = (
                db.query(
                    ForumTopicModel,
                    func.coalesce(post_stats.c.reply_count, 0).label("reply_count"),
                    post_stats.c.latest_post_at,
                    post_stats.c.unique_reply_users,
                    favorite_stats.c.unique_users,
                    favorite_stats.c.latest_interacted_at,
                    like_stats.c.unique_users,
                    like_stats.c.latest_interacted_at,
                    interactor_stats.c.unique_interactors,
                )
                .outerjoin(
                    post_stats,
                    ForumTopicModel.topic_id == post_stats.c.topic_id,
                )
                .outerjoin(
                    favorite_stats,
                    ForumTopicModel.topic_id == favorite_stats.c.topic_id,
                )
                .outerjoin(
                    like_stats,
                    ForumTopicModel.topic_id == like_stats.c.topic_id,
                )
                .outerjoin(
                    interactor_stats,
                    ForumTopicModel.topic_id == interactor_stats.c.topic_id,
                )
                .filter(
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                )
            )
            if status is not None:
                query = query.filter(ForumTopicModel.status == status)
            if topic_type is not None:
                query = query.filter(ForumTopicModel.topic_type == topic_type)
            if keyword is not None:
                query = query.filter(
                    or_(
                        ForumTopicModel.title.contains(keyword, autoescape=True),
                        ForumTopicModel.body.contains(keyword, autoescape=True),
                    )
                )
            if author_id is not None:
                query = query.filter(ForumTopicModel.author_id == author_id)
            if followed_authors is not None:
                if not followed_authors:
                    return ForumTopicPage(total=0, items=())
                query = query.filter(
                    tuple_(
                        ForumTopicModel.author_type,
                        ForumTopicModel.author_id,
                    ).in_(
                        (author.type.value, author.id)
                        for author in followed_authors
                    )
                )

            total = query.count()
            ranked_rows = []
            now = datetime.now(timezone.utc)
            for row in query.all():
                topic = row[0]
                latest_activity_at = row[2] or topic.gmt_create
                heat_equivalent = (
                    int(row[3] or 0) + 3 * int(row[4] or 0) + 2 * int(row[6] or 0)
                )
                latest_hot_at = max(
                    (
                        topic.gmt_create,
                        row[2],
                        row[5],
                        row[7],
                    ),
                    key=lambda value: value or topic.gmt_create,
                )
                age_hours = max(
                    0.0,
                    (now - latest_hot_at.replace(tzinfo=timezone.utc)).total_seconds()
                    / 3600,
                )
                hot_rank = heat_equivalent * 0.5 ** (age_hours / 24)
                is_hot = (
                    age_hours <= 72 and int(row[8] or 0) >= 2 and heat_equivalent >= 10
                )
                ranked_rows.append(
                    (
                        not is_hot,
                        -hot_rank,
                        -latest_activity_at.replace(tzinfo=timezone.utc).timestamp(),
                        -topic.id,
                        topic,
                        int(row[1] or 0),
                        latest_activity_at,
                        is_hot,
                    )
                )
            ranked_rows.sort(key=lambda row: row[:4])
            page_rows = ranked_rows[offset : offset + limit]
            items = tuple(
                replace(
                    row[4].to_record(),
                    reply_count=row[5],
                    latest_activity_at=row[6],
                    is_hot=row[7],
                )
                for row in page_rows
            )
            return ForumTopicPage(total=total, items=items)

    def list_followed_authors(
        self,
        *,
        owner_user_id: str,
    ) -> tuple[ForumActorRef, ...]:
        with self._db.orm_session() as db:
            rows = (
                db.query(
                    ForumInteractionModel.target_type,
                    ForumInteractionModel.target_id,
                )
                .filter(
                    ForumInteractionModel.env == get_current_env(),
                    ForumInteractionModel.actor_type == ForumActorType.HUMAN,
                    ForumInteractionModel.actor_id == owner_user_id,
                    ForumInteractionModel.action_type
                    == ForumInteractionActionType.FOLLOW,
                    ForumInteractionModel.target_type.in_(
                        (
                            ForumInteractionTargetType.HUMAN,
                            ForumInteractionTargetType.BOT,
                        )
                    ),
                    ForumInteractionModel.active > 0,
                )
                .order_by(
                    ForumInteractionModel.target_type.asc(),
                    ForumInteractionModel.target_id.asc(),
                )
                .all()
            )
            return tuple(
                ForumActorRef(type=ForumActorType(target_type), id=target_id)
                for target_type, target_id in rows
            )

    def _topic_interaction_stats(self, db, action_type):
        return (
            db.query(
                ForumInteractionModel.target_id.label("topic_id"),
                func.count(
                    func.distinct(
                        func.concat(
                            ForumInteractionModel.actor_type,
                            ":",
                            ForumInteractionModel.actor_id,
                        )
                    )
                ).label("unique_users"),
                func.max(ForumInteractionModel.gmt_create).label(
                    "latest_interacted_at"
                ),
            )
            .filter(
                ForumInteractionModel.env == get_current_env(),
                ForumInteractionModel.action_type == action_type.value,
                ForumInteractionModel.target_type
                == ForumInteractionTargetType.TOPIC.value,
            )
            .group_by(ForumInteractionModel.target_id)
            .subquery()
        )

    def _topic_actor_rows(self, db, action_type):
        return db.query(
            ForumInteractionModel.target_id.label("topic_id"),
            ForumInteractionModel.actor_type.label("actor_type"),
            ForumInteractionModel.actor_id.label("actor_id"),
        ).filter(
            ForumInteractionModel.env == get_current_env(),
            ForumInteractionModel.action_type == action_type.value,
            ForumInteractionModel.target_type == ForumInteractionTargetType.TOPIC.value,
        )
