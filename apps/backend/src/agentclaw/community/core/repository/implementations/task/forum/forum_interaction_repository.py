"""Persistence and aggregate queries for BBS interaction events."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from injector import inject
from sqlalchemy import and_, func, or_, tuple_

from agentclaw.community.core.repository.implementations.task.forum.forum_repository import (
    _helpful_from_post_config,
)
from agentclaw.community.core.repository.protocols.task.forum_interaction import (
    ForumInteractionRepositoryProtocol,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorTargetRef,
    ForumActorType,
    ForumInteractionActionType,
    ForumInteractionRecord,
    ForumInteractionTargetType,
    ForumStatsRecord,
    ForumTargetRef,
)
from agentclaw.community.core.task.forum.repository.models import (
    ForumInteractionModel,
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

_ACTION_TO_FIELD = {
    ForumInteractionActionType.VIEW: "view_count",
    ForumInteractionActionType.LIKE: "like_count",
    ForumInteractionActionType.FAVORITE: "favorite_count",
    ForumInteractionActionType.FOLLOW: "follow_count",
    ForumInteractionActionType.ACCEPT: "accept_count",
}


def _stats_from_event_counts(
    rows: list[tuple[str, int]], *, actor_scope: bool = False
) -> ForumStatsRecord:
    stats = ForumStatsRecord()
    for action, count in rows:
        field = _ACTION_TO_FIELD[ForumInteractionActionType(action)]
        stats = replace(stats, **{field: int(count)})
    return (
        stats
        if not actor_scope
        else replace(
            stats,
            view_count=0,
            like_count=0,
            favorite_count=0,
            accept_count=0,
        )
    )


def _actor_ref(actor_type: str, actor_id: str) -> ForumActorRef:
    return ForumActorRef(type=ForumActorType(actor_type), id=actor_id)


def _target_ref(target_type: str, target_id: str) -> ForumTargetRef:
    return ForumTargetRef(type=ForumInteractionTargetType(target_type), id=target_id)


def _topic_projection_counts(
    db, topic_ids: tuple[str, ...]
) -> dict[str, tuple[int, int]]:
    """Return ``reply_participant_count`` and ``helpful_reply_count``."""

    if not topic_ids:
        return {}
    rows = (
        db.query(
            ForumPostModel.topic_id,
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
            ForumPostModel.topic_id.in_(topic_ids),
        )
        .all()
    )
    result = {topic_id: (0, 0) for topic_id in topic_ids}
    participants = {topic_id: set() for topic_id in topic_ids}
    for topic_id, author_type, author_id, reply_to_post_id, post_config in rows:
        participants[topic_id].add((author_type, author_id))
        if reply_to_post_id is None and _helpful_from_post_config(post_config):
            participant_count, helpful_count = result[topic_id]
            result[topic_id] = (participant_count, helpful_count + 1)
    for topic_id, identities in participants.items():
        participant_count, helpful_count = result[topic_id]
        result[topic_id] = (len(identities), helpful_count)
    return result


class ForumInteractionRepository(ForumInteractionRepositoryProtocol):
    """Store events and calculate immediate aggregates without counters."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def insert_interaction(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action_type: str,
        target_type: str,
        target_id: str,
    ) -> ForumInteractionRecord:
        now = datetime.now(timezone.utc)
        row = ForumInteractionModel(
            actor_type=actor_type,
            actor_id=actor_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            env=get_current_env(),
            gmt_create=now,
            gmt_modified=now,
        )
        with self._db.orm_session() as db:
            db.add(row)
            db.flush()
            return row.to_record()

    def get_summary(
        self,
        *,
        actor_type: str | None = None,
        actor_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> ForumStatsRecord:
        if actor_type is not None and actor_id is not None:
            actor = _actor_ref(actor_type, actor_id)
            if target_type is not None and target_id is not None:
                pair = ForumActorTargetRef(
                    actor=actor, target=_target_ref(target_type, target_id)
                )
                return self.batch_get_actor_target_summaries((pair,))[pair]
            return self.batch_get_actor_summaries((actor,))[actor]
        return self.batch_get_target_summaries((_target_ref(target_type, target_id),))[
            _target_ref(target_type, target_id)
        ]

    def batch_get_actor_summaries(
        self, actor_refs: tuple[ForumActorRef, ...]
    ) -> dict[ForumActorRef, ForumStatsRecord]:
        unique_refs = tuple(dict.fromkeys(actor_refs))
        result = {ref: ForumStatsRecord() for ref in unique_refs}
        if not unique_refs:
            return result
        with self._db.orm_session() as db:
            rows = (
                db.query(
                    ForumInteractionModel.target_type,
                    ForumInteractionModel.target_id,
                    func.count().label("event_count"),
                )
                .filter(
                    ForumInteractionModel.env == get_current_env(),
                    tuple_(
                        ForumInteractionModel.target_type,
                        ForumInteractionModel.target_id,
                    ).in_((ref.type.value, ref.id) for ref in unique_refs),
                    ForumInteractionModel.action_type
                    == ForumInteractionActionType.FOLLOW,
                )
                .group_by(
                    ForumInteractionModel.target_type,
                    ForumInteractionModel.target_id,
                )
                .all()
            )
        for actor_type, actor_id, count in rows:
            ref = _actor_ref(actor_type, actor_id)
            if ref not in result:
                continue
            result[ref] = _stats_from_event_counts(
                [(ForumInteractionActionType.FOLLOW, count)], actor_scope=True
            )
        return result

    def batch_get_target_summaries(
        self, target_refs: tuple[ForumTargetRef, ...]
    ) -> dict[ForumTargetRef, ForumStatsRecord]:
        unique_refs = tuple(dict.fromkeys(target_refs))
        result = {ref: ForumStatsRecord() for ref in unique_refs}
        if not unique_refs:
            return result
        with self._db.orm_session() as db:
            rows = (
                db.query(
                    ForumInteractionModel.target_type,
                    ForumInteractionModel.target_id,
                    ForumInteractionModel.action_type,
                    func.count().label("event_count"),
                )
                .filter(
                    ForumInteractionModel.env == get_current_env(),
                    tuple_(
                        ForumInteractionModel.target_type,
                        ForumInteractionModel.target_id,
                    ).in_((ref.type.value, ref.id) for ref in unique_refs),
                )
                .group_by(
                    ForumInteractionModel.target_type,
                    ForumInteractionModel.target_id,
                    ForumInteractionModel.action_type,
                )
                .all()
            )
            topic_refs = tuple(
                ref
                for ref in unique_refs
                if ref.type is ForumInteractionTargetType.TOPIC
            )
            projections = _topic_projection_counts(
                db, tuple(ref.id for ref in topic_refs)
            )

        grouped_counts: dict[ForumTargetRef, list[tuple[str, int]]] = {
            ref: [] for ref in unique_refs
        }
        for target_type, target_id, action, count in rows:
            grouped_counts[_target_ref(target_type, target_id)].append(
                (action, int(count))
            )
        for ref, action_rows in grouped_counts.items():
            stats = _stats_from_event_counts(action_rows)
            if ref.type is ForumInteractionTargetType.TOPIC:
                participant_count, helpful_count = projections.get(ref.id, (0, 0))
                stats = replace(
                    stats,
                    reply_participant_count=participant_count,
                    helpful_reply_count=helpful_count,
                )
            result[ref] = stats
        return result

    def batch_get_actor_target_summaries(
        self, pairs: tuple[ForumActorTargetRef, ...]
    ) -> dict[ForumActorTargetRef, ForumStatsRecord]:
        unique_pairs = tuple(dict.fromkeys(pairs))
        result = {pair: ForumStatsRecord() for pair in unique_pairs}
        if not unique_pairs:
            return result
        with self._db.orm_session() as db:
            rows = (
                db.query(
                    ForumInteractionModel.actor_type,
                    ForumInteractionModel.actor_id,
                    ForumInteractionModel.target_type,
                    ForumInteractionModel.target_id,
                    ForumInteractionModel.action_type,
                    func.count().label("event_count"),
                )
                .filter(
                    ForumInteractionModel.env == get_current_env(),
                    or_(
                        *(
                            and_(
                                ForumInteractionModel.actor_type
                                == pair.actor.type.value,
                                ForumInteractionModel.actor_id == pair.actor.id,
                                ForumInteractionModel.target_type
                                == pair.target.type.value,
                                ForumInteractionModel.target_id == pair.target.id,
                            )
                            for pair in unique_pairs
                        )
                    ),
                )
                .group_by(
                    ForumInteractionModel.actor_type,
                    ForumInteractionModel.actor_id,
                    ForumInteractionModel.target_type,
                    ForumInteractionModel.target_id,
                    ForumInteractionModel.action_type,
                )
                .all()
            )

        grouped_rows = {pair: [] for pair in unique_pairs}
        for actor_type, actor_id, target_type, target_id, action, count in rows:
            pair = ForumActorTargetRef(
                actor=_actor_ref(actor_type, actor_id),
                target=_target_ref(target_type, target_id),
            )
            if pair in grouped_rows:
                grouped_rows[pair].append((action, int(count)))
        for pair, action_rows in grouped_rows.items():
            result[pair] = _stats_from_event_counts(action_rows)
        return result
