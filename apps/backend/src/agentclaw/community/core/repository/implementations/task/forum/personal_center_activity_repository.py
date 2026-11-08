"""Server-side unified activity projection for the BBS personal center."""

from __future__ import annotations

from injector import inject
from sqlalchemy import and_, func, literal, null, tuple_, union_all

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
    PersonalCenterActivityType.REPLIED: 3,
    PersonalCenterActivityType.AUTHORED: 4,
}

_TOPIC_ACTIVITY_TYPES = frozenset(
    {
        PersonalCenterActivityType.FAVORITED,
        PersonalCenterActivityType.VIEWED,
    }
)
_LIKED_ACTIVITY_TYPES = (PersonalCenterActivityType.LIKED,)


def _actor_pairs(actors: tuple[ForumActorRef, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((actor.type.value, actor.id) for actor in dict.fromkeys(actors))


def _selected_topic_query(
    db,
    *,
    activity_type: PersonalCenterActivityType,
    action_type: ForumInteractionActionType,
    actors: tuple[ForumActorRef, ...],
):
    deduped = (
        db.query(
            ForumInteractionModel.actor_type.label("actor_type"),
            ForumInteractionModel.actor_id.label("actor_id"),
            ForumInteractionModel.target_type.label("target_type"),
            ForumInteractionModel.target_id.label("target_id"),
            func.max(ForumInteractionModel.gmt_create).label("happened_at"),
            func.max(ForumInteractionModel.id).label("source_id"),
        )
        .filter(
            ForumInteractionModel.env == get_current_env(),
            tuple_(
                ForumInteractionModel.actor_type, ForumInteractionModel.actor_id
            ).in_(_actor_pairs(actors)),
            ForumInteractionModel.action_type == action_type,
            ForumInteractionModel.target_type == ForumInteractionTargetType.TOPIC,
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
    deduped = (
        db.query(
            ForumInteractionModel.actor_type.label("actor_type"),
            ForumInteractionModel.actor_id.label("actor_id"),
            ForumInteractionModel.target_type.label("target_type"),
            ForumInteractionModel.target_id.label("target_id"),
            func.max(ForumInteractionModel.gmt_create).label("happened_at"),
            func.max(ForumInteractionModel.id).label("source_id"),
        )
        .filter(
            ForumInteractionModel.env == get_current_env(),
            tuple_(
                ForumInteractionModel.actor_type, ForumInteractionModel.actor_id
            ).in_(_actor_pairs(actors)),
            ForumInteractionModel.action_type == ForumInteractionActionType.LIKE,
            ForumInteractionModel.target_type == ForumInteractionTargetType.POST,
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
        literal(_ACTIVITY_ORDER[PersonalCenterActivityType.REPLIED]).label(
            "source_order"
        ),
        ForumPostModel.id.label("source_id"),
    ).join(
        ForumTopicModel,
        ForumTopicModel.topic_id == ForumPostModel.topic_id,
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

        with self._db.orm_session() as db:
            selected: list = []
            if (
                activity_type is PersonalCenterActivityType.AUTHORED
                or activity_type is PersonalCenterActivityType.ALL
            ):
                selected.append(_authored_query(db, actors=actors))
            if (
                activity_type in _TOPIC_ACTIVITY_TYPES
                or activity_type is PersonalCenterActivityType.ALL
            ):
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
                activity_type in _LIKED_ACTIVITY_TYPES
                or activity_type is PersonalCenterActivityType.ALL
            ):
                if target_type is not ForumInteractionTargetType.POST:
                    selected.append(
                        _selected_topic_query(
                            db,
                            activity_type=PersonalCenterActivityType.LIKED,
                            action_type=ForumInteractionActionType.LIKE,
                            actors=actors,
                        )
                    )
                if target_type is not ForumInteractionTargetType.TOPIC:
                    selected.append(_liked_post_query(db, actors=actors))
            if (
                activity_type is PersonalCenterActivityType.REPLIED
                or activity_type is PersonalCenterActivityType.ALL
            ):
                if target_type is not ForumInteractionTargetType.POST:
                    selected.append(_replied_query(db, actors=actors, direct=True))
                if target_type is not ForumInteractionTargetType.TOPIC:
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
                activity_type=PersonalCenterActivityType(row[0]),
                actor_type=ForumActorType(row[1]),
                actor_id=row[2],
                target_type=ForumInteractionTargetType(row[3]),
                target_id=row[4],
                topic_id=row[5],
                post_id=row[6],
                happened_at=row[7],
                body_preview=(row[8] or "")[:500],
            )
            for row in rows
        )
        return PersonalCenterActivityPage(total=total, items=items)
