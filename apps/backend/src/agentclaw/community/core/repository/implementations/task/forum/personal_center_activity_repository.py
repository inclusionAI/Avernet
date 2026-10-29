"""Server-side unified activity projection for the BBS personal center."""

from __future__ import annotations

from injector import inject
from sqlalchemy import DateTime, String, and_, func, literal, null, tuple_, union_all
from sqlalchemy.orm import aliased

from agentclaw.community.core.repository.protocols.task.forum_personal_center_activity import (
    ForumPersonalCenterActivityRepositoryProtocol,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorRef,
    ForumActorType,
    ForumInteractionActionType,
    ForumInteractionTargetType,
    PersonalCenterActivityPage,
    PersonalCenterActivityRecord,
    PersonalCenterActivityType,
    PersonalCenterActorScope,
    PersonalCenterReplyContext,
    PersonalCenterTopicContext,
)
from agentclaw.community.core.task.forum.repository.models import (
    ForumInteractionModel,
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

_ACTIVITY_ORDER = {
    PersonalCenterActivityType.VIEWED: 0,
    PersonalCenterActivityType.FAVORITED: 1,
    PersonalCenterActivityType.LIKED: 2,
    PersonalCenterActivityType.FOLLOWED: 3,
    PersonalCenterActivityType.REPLIED: 4,
    PersonalCenterActivityType.AUTHORED: 5,
}

_TOPIC_ACTIVITY_TYPES = frozenset(
    {
        PersonalCenterActivityType.FAVORITED,
        PersonalCenterActivityType.VIEWED,
    }
)
_LIKED_ACTIVITY_TYPES = (PersonalCenterActivityType.LIKED,)
_FOLLOWED_ACTIVITY_TYPES = (PersonalCenterActivityType.FOLLOWED,)


def _actor_pairs(actors: tuple[ForumActorRef, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((actor.type.value, actor.id) for actor in dict.fromkeys(actors))


def _topic_reply_count_subquery(db):
    return (
        db.query(func.count(ForumPostModel.id))
        .filter(ForumPostModel.topic_id == ForumTopicModel.topic_id)
        .correlate(ForumTopicModel)
        .scalar_subquery()
    )


def _topic_latest_reply_at_subquery(db):
    return (
        db.query(func.max(ForumPostModel.gmt_create))
        .filter(ForumPostModel.topic_id == ForumTopicModel.topic_id)
        .correlate(ForumTopicModel)
        .scalar_subquery()
    )


def _topic_like_count_subquery(db):
    return (
        db.query(func.coalesce(func.sum(ForumInteractionModel.active), 0))
        .filter(
            ForumInteractionModel.env == get_current_env(),
            ForumInteractionModel.action_type == ForumInteractionActionType.LIKE,
            ForumInteractionModel.target_type == ForumInteractionTargetType.TOPIC,
            ForumInteractionModel.target_id == ForumTopicModel.topic_id,
            ForumInteractionModel.active > 0,
        )
        .correlate(ForumTopicModel)
        .scalar_subquery()
    )


def _topic_context_columns(db):
    return [
        ForumTopicModel.title.label("topic_title"),
        ForumTopicModel.body.label("topic_body"),
        ForumTopicModel.topic_type.label("topic_type"),
        ForumTopicModel.author_type.label("topic_author_type"),
        ForumTopicModel.author_id.label("topic_author_id"),
        func.coalesce(
            ForumTopicModel.author_display_name, ForumTopicModel.author_id
        ).label("topic_author_name"),
        _topic_reply_count_subquery(db).label("topic_reply_count"),
        _topic_like_count_subquery(db).label("topic_like_count"),
        _topic_latest_reply_at_subquery(db).label("topic_latest_reply_at"),
    ]


def _topic_context_null_columns():
    return [
        literal(None, String).label("topic_title"),
        literal(None, String).label("topic_body"),
        literal(None, String).label("topic_type"),
        literal(None, String).label("topic_author_type"),
        literal(None, String).label("topic_author_id"),
        literal(None, String).label("topic_author_name"),
        null().label("topic_reply_count"),
        null().label("topic_like_count"),
        literal(None, DateTime).label("topic_latest_reply_at"),
    ]


def _reply_context_null_columns():
    return [
        literal(None, String).label("replied_to_post_id"),
        literal(None, String).label("replied_to_author_type"),
        literal(None, String).label("replied_to_author_id"),
        literal(None, String).label("replied_to_author_name"),
        literal(None, String).label("replied_to_body"),
    ]


def _topic_context_from_row(
    row,
) -> PersonalCenterTopicContext | None:
    if row.topic_title is None:
        return None
    author_type = str(row.topic_author_type or "HUMAN")
    return PersonalCenterTopicContext(
        title=row.topic_title,
        body_preview=(row.topic_body or "")[:500],
        topic_type=row.topic_type or "discussion",
        author_type=ForumActorType(author_type),
        author_id=row.topic_author_id or "",
        author_display_name=row.topic_author_name or row.topic_author_id or "",
        reply_count=max(0, int(row.topic_reply_count or 0)),
        like_count=max(0, int(row.topic_like_count or 0)),
        latest_reply_at=row.topic_latest_reply_at,
    )


def _reply_context_from_row(
    row,
) -> PersonalCenterReplyContext | None:
    if row.replied_to_post_id is None:
        return None
    author_type = str(row.replied_to_author_type or "HUMAN")
    return PersonalCenterReplyContext(
        post_id=row.replied_to_post_id,
        author_type=ForumActorType(author_type),
        author_id=row.replied_to_author_id or "",
        author_display_name=row.replied_to_author_name or row.replied_to_author_id or "",
        body_preview=(row.replied_to_body or "")[:500],
    )


def _latest_active_interaction_rows(db, *filters):
    """Return the latest active row for each full interaction action key."""
    latest_ids = (
        db.query(func.max(ForumInteractionModel.id).label("id"))
        .filter(ForumInteractionModel.env == get_current_env(), *filters)
        .group_by(
            ForumInteractionModel.env,
            ForumInteractionModel.actor_type,
            ForumInteractionModel.actor_id,
            ForumInteractionModel.action_type,
            ForumInteractionModel.target_type,
            ForumInteractionModel.target_id,
        )
        .subquery()
    )
    return (
        db.query(ForumInteractionModel)
        .join(latest_ids, ForumInteractionModel.id == latest_ids.c.id)
        .filter(ForumInteractionModel.active > 0)
    )


def _selected_topic_query(
    db,
    *,
    activity_type: PersonalCenterActivityType,
    action_type: ForumInteractionActionType,
    actors: tuple[ForumActorRef, ...],
):
    latest_rows = _latest_active_interaction_rows(
        db,
        tuple_(
            ForumInteractionModel.actor_type, ForumInteractionModel.actor_id
        ).in_(_actor_pairs(actors)),
        ForumInteractionModel.action_type == action_type,
        ForumInteractionModel.target_type == ForumInteractionTargetType.TOPIC,
    )
    deduped = (
        latest_rows.with_entities(
            ForumInteractionModel.actor_type.label("actor_type"),
            ForumInteractionModel.actor_id.label("actor_id"),
            ForumInteractionModel.target_type.label("target_type"),
            ForumInteractionModel.target_id.label("target_id"),
            func.max(ForumInteractionModel.gmt_create).label("happened_at"),
            func.max(ForumInteractionModel.id).label("source_id"),
        )
        .group_by(
            ForumInteractionModel.actor_type,
            ForumInteractionModel.actor_id,
            ForumInteractionModel.target_type,
            ForumInteractionModel.target_id,
        )
        .subquery()
    )
    return (
        db.query(
            literal(activity_type.value).label("activity_type"),
            deduped.c.actor_type.label("actor_type"),
            deduped.c.actor_id.label("actor_id"),
            deduped.c.target_type.label("target_type"),
            deduped.c.target_id.label("target_id"),
            ForumTopicModel.topic_id.label("topic_id"),
            null().label("post_id"),
            deduped.c.happened_at.label("happened_at"),
            ForumTopicModel.title.label("body_preview"),
            *_topic_context_columns(db),
            *_reply_context_null_columns(),
            literal(_ACTIVITY_ORDER[activity_type]).label("source_order"),
            deduped.c.source_id.label("source_id"),
        )
        .select_from(deduped)
        .join(
            ForumTopicModel,
            and_(
                ForumTopicModel.topic_id == deduped.c.target_id,
                ForumTopicModel.env == get_current_env(),
                ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
            ),
        )
    )


def _liked_post_query(
    db,
    *,
    actors: tuple[ForumActorRef, ...],
):
    latest_rows = _latest_active_interaction_rows(
        db,
        tuple_(
            ForumInteractionModel.actor_type, ForumInteractionModel.actor_id
        ).in_(_actor_pairs(actors)),
        ForumInteractionModel.action_type == ForumInteractionActionType.LIKE,
        ForumInteractionModel.target_type == ForumInteractionTargetType.POST,
    )
    deduped = (
        latest_rows.with_entities(
            ForumInteractionModel.actor_type.label("actor_type"),
            ForumInteractionModel.actor_id.label("actor_id"),
            ForumInteractionModel.target_type.label("target_type"),
            ForumInteractionModel.target_id.label("target_id"),
            func.max(ForumInteractionModel.gmt_create).label("happened_at"),
            func.max(ForumInteractionModel.id).label("source_id"),
        )
        .group_by(
            ForumInteractionModel.actor_type,
            ForumInteractionModel.actor_id,
            ForumInteractionModel.target_type,
            ForumInteractionModel.target_id,
        )
        .subquery()
    )
    return (
        db.query(
            literal(PersonalCenterActivityType.LIKED.value).label("activity_type"),
            deduped.c.actor_type.label("actor_type"),
            deduped.c.actor_id.label("actor_id"),
            deduped.c.target_type.label("target_type"),
            deduped.c.target_id.label("target_id"),
            ForumPostModel.topic_id.label("topic_id"),
            deduped.c.target_id.label("post_id"),
            deduped.c.happened_at.label("happened_at"),
            ForumPostModel.body.label("body_preview"),
            *_topic_context_columns(db),
            *_reply_context_null_columns(),
            literal(_ACTIVITY_ORDER[PersonalCenterActivityType.LIKED]).label(
                "source_order"
            ),
            deduped.c.source_id.label("source_id"),
        )
        .select_from(deduped)
        .outerjoin(
            ForumPostModel,
            ForumPostModel.post_id == deduped.c.target_id,
        )
        .join(
            ForumTopicModel,
            and_(
                ForumTopicModel.topic_id == ForumPostModel.topic_id,
                ForumTopicModel.env == get_current_env(),
                ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
            ),
        )
    )


def _followed_identity_query(
    db,
    *,
    actors: tuple[ForumActorRef, ...],
    target_type: ForumInteractionTargetType | None,
):
    latest_rows = _latest_active_interaction_rows(
        db,
        tuple_(
            ForumInteractionModel.actor_type, ForumInteractionModel.actor_id
        ).in_(_actor_pairs(actors)),
        ForumInteractionModel.action_type == ForumInteractionActionType.FOLLOW,
        (
            ForumInteractionModel.target_type == target_type
            if target_type is not None
            else ForumInteractionModel.target_type.in_(
                (
                    ForumInteractionTargetType.HUMAN,
                    ForumInteractionTargetType.BOT,
                )
            )
        ),
    )
    deduped = (
        latest_rows.with_entities(
            ForumInteractionModel.actor_type.label("actor_type"),
            ForumInteractionModel.actor_id.label("actor_id"),
            ForumInteractionModel.target_type.label("target_type"),
            ForumInteractionModel.target_id.label("target_id"),
            func.max(ForumInteractionModel.gmt_create).label("happened_at"),
            func.max(ForumInteractionModel.id).label("source_id"),
        )
        .group_by(
            ForumInteractionModel.actor_type,
            ForumInteractionModel.actor_id,
            ForumInteractionModel.target_type,
            ForumInteractionModel.target_id,
        )
        .subquery()
    )
    display_name_subquery = (
        db.query(ForumTopicModel.author_display_name)
        .filter(
            ForumTopicModel.author_type == deduped.c.target_type,
            ForumTopicModel.author_id == deduped.c.target_id,
            ForumTopicModel.env == get_current_env(),
            ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
        )
        .order_by(ForumTopicModel.id.desc())
        .limit(1)
        .correlate(deduped)
        .scalar_subquery()
    )
    return db.query(
        literal(PersonalCenterActivityType.FOLLOWED.value).label("activity_type"),
        deduped.c.actor_type.label("actor_type"),
        deduped.c.actor_id.label("actor_id"),
        deduped.c.target_type.label("target_type"),
        deduped.c.target_id.label("target_id"),
        null().label("topic_id"),
        null().label("post_id"),
        deduped.c.happened_at.label("happened_at"),
        func.coalesce(display_name_subquery, deduped.c.target_id).label(
            "body_preview"
        ),
        *_topic_context_null_columns(),
        *_reply_context_null_columns(),
        literal(_ACTIVITY_ORDER[PersonalCenterActivityType.FOLLOWED]).label(
            "source_order"
        ),
        deduped.c.source_id.label("source_id"),
    ).select_from(deduped)


def _authored_query(
    db,
    *,
    actors: tuple[ForumActorRef, ...],
):
    return db.query(
        literal(PersonalCenterActivityType.AUTHORED.value).label("activity_type"),
        ForumTopicModel.author_type.label("actor_type"),
        ForumTopicModel.author_id.label("actor_id"),
        literal(ForumInteractionTargetType.TOPIC.value).label("target_type"),
        ForumTopicModel.topic_id.label("target_id"),
        ForumTopicModel.topic_id.label("topic_id"),
        null().label("post_id"),
        ForumTopicModel.gmt_create.label("happened_at"),
        ForumTopicModel.title.label("body_preview"),
        *_topic_context_columns(db),
        *_reply_context_null_columns(),
        literal(_ACTIVITY_ORDER[PersonalCenterActivityType.AUTHORED]).label(
            "source_order"
        ),
        ForumTopicModel.id.label("source_id"),
    ).filter(
        ForumTopicModel.env == get_current_env(),
        ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
        tuple_(ForumTopicModel.author_type, ForumTopicModel.author_id).in_(
            _actor_pairs(actors)
        ),
    )


def _replied_query(
    db,
    *,
    actors: tuple[ForumActorRef, ...],
    direct: bool,
):
    parent_post = aliased(ForumPostModel, name="replied_to_post")
    query = db.query(
        literal(PersonalCenterActivityType.REPLIED.value).label("activity_type"),
        ForumPostModel.author_type.label("actor_type"),
        ForumPostModel.author_id.label("actor_id"),
        literal(
            (
                ForumInteractionTargetType.TOPIC
                if direct
                else ForumInteractionTargetType.POST
            ).value
        ).label("target_type"),
        ForumPostModel.topic_id.label("target_id")
        if direct
        else ForumPostModel.reply_to_post_id.label("target_id"),
        ForumPostModel.topic_id.label("topic_id"),
        ForumPostModel.post_id.label("post_id"),
        ForumPostModel.gmt_create.label("happened_at"),
        ForumPostModel.body.label("body_preview"),
        *_topic_context_columns(db),
        *(
            _reply_context_null_columns()
            if direct
            else [
                parent_post.post_id.label("replied_to_post_id"),
                parent_post.author_type.label("replied_to_author_type"),
                parent_post.author_id.label("replied_to_author_id"),
                func.coalesce(
                    parent_post.author_display_name,
                    parent_post.author_id,
                ).label("replied_to_author_name"),
                parent_post.body.label("replied_to_body"),
            ]
        ),
        literal(_ACTIVITY_ORDER[PersonalCenterActivityType.REPLIED]).label(
            "source_order"
        ),
        ForumPostModel.id.label("source_id"),
    ).join(
        ForumTopicModel,
        ForumTopicModel.topic_id == ForumPostModel.topic_id,
    )
    if not direct:
        query = query.outerjoin(
            parent_post,
            parent_post.post_id == ForumPostModel.reply_to_post_id,
        )
    filters = [
        ForumTopicModel.env == get_current_env(),
        ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
        tuple_(ForumPostModel.author_type, ForumPostModel.author_id).in_(
            _actor_pairs(actors)
        ),
    ]
    if direct:
        filters.append(ForumPostModel.reply_to_post_id.is_(None))
    else:
        filters.append(ForumPostModel.reply_to_post_id.isnot(None))
    return query.filter(*filters)


class ForumPersonalCenterActivityRepository(
    ForumPersonalCenterActivityRepositoryProtocol
):
    """Project and page all activity sources in one unified query."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

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
        if not actors:
            return PersonalCenterActivityPage(total=0, items=())
        if target_type is not ForumInteractionTargetType.TOPIC and (
            activity_type in _TOPIC_ACTIVITY_TYPES
            or activity_type is PersonalCenterActivityType.AUTHORED
        ):
            if target_type is not None:
                return PersonalCenterActivityPage(total=0, items=())
        if activity_type in _FOLLOWED_ACTIVITY_TYPES:
            if target_type not in (
                None,
                ForumInteractionTargetType.HUMAN,
                ForumInteractionTargetType.BOT,
            ):
                return PersonalCenterActivityPage(total=0, items=())

        with self._db.orm_session() as db:
            selected: list = []
            if (
                activity_type is PersonalCenterActivityType.AUTHORED
                or activity_type is PersonalCenterActivityType.ALL
            ) and target_type in (None, ForumInteractionTargetType.TOPIC):
                selected.append(_authored_query(db, actors=actors))
            if (
                activity_type in _TOPIC_ACTIVITY_TYPES
                or activity_type is PersonalCenterActivityType.ALL
            ) and target_type in (None, ForumInteractionTargetType.TOPIC):
                if activity_type is PersonalCenterActivityType.ALL:
                    selected.append(
                        _selected_topic_query(
                            db,
                            activity_type=PersonalCenterActivityType.FAVORITED,
                            action_type=ForumInteractionActionType.FAVORITE,
                            actors=actors,
                        )
                    )
                    selected.append(
                        _selected_topic_query(
                            db,
                            activity_type=PersonalCenterActivityType.VIEWED,
                            action_type=ForumInteractionActionType.VIEW,
                            actors=actors,
                        )
                    )
                else:
                    selected.append(
                        _selected_topic_query(
                            db,
                            activity_type=activity_type,
                            action_type=ForumInteractionActionType.FAVORITE
                            if activity_type is PersonalCenterActivityType.FAVORITED
                            else ForumInteractionActionType.VIEW,
                            actors=actors,
                        )
                    )
            if (
                activity_type in _FOLLOWED_ACTIVITY_TYPES
                or activity_type is PersonalCenterActivityType.ALL
            ) and target_type in (
                None,
                ForumInteractionTargetType.HUMAN,
                ForumInteractionTargetType.BOT,
            ):
                selected.append(
                    _followed_identity_query(
                        db,
                        actors=actors,
                        target_type=target_type,
                    )
                )
            if (
                activity_type in _LIKED_ACTIVITY_TYPES
                or activity_type is PersonalCenterActivityType.ALL
            ):
                if target_type in (None, ForumInteractionTargetType.TOPIC):
                    selected.append(
                        _selected_topic_query(
                            db,
                            activity_type=PersonalCenterActivityType.LIKED,
                            action_type=ForumInteractionActionType.LIKE,
                            actors=actors,
                        )
                    )
                if target_type in (None, ForumInteractionTargetType.POST):
                    selected.append(_liked_post_query(db, actors=actors))
            if (
                activity_type is PersonalCenterActivityType.REPLIED
                or activity_type is PersonalCenterActivityType.ALL
            ):
                if target_type in (None, ForumInteractionTargetType.TOPIC):
                    selected.append(_replied_query(db, actors=actors, direct=True))
                if target_type in (None, ForumInteractionTargetType.POST):
                    selected.append(_replied_query(db, actors=actors, direct=False))

            combined_query = selected[0]
            if len(selected) > 1:
                combined_query = union_all(*selected)
            projection = combined_query.subquery()
            ordered = db.query(projection).order_by(
                projection.c.happened_at.desc(),
                projection.c.source_order.desc(),
                projection.c.source_id.desc(),
            )
            total = ordered.count()
            rows = ordered.offset((page - 1) * page_size).limit(page_size).all()

        items = tuple(
            PersonalCenterActivityRecord(
                activity_type=PersonalCenterActivityType(row.activity_type),
                actor_type=ForumActorType(row.actor_type),
                actor_id=row.actor_id,
                target_type=ForumInteractionTargetType(row.target_type),
                target_id=row.target_id,
                topic_id=row.topic_id,
                post_id=row.post_id,
                happened_at=row.happened_at,
                body_preview=(row.body_preview or "")[:500],
                topic_context=_topic_context_from_row(row),
                replied_to_post=_reply_context_from_row(row),
            )
            for row in rows
        )
        return PersonalCenterActivityPage(total=total, items=items)
