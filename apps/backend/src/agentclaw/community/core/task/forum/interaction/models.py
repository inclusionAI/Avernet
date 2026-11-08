"""Immutable records for the BBS interaction domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Optional

MAX_SUMMARY_BATCH_ITEMS = 100
MAX_TARGET_ID_LENGTH = 256


class ForumActorType(StrEnum):
    """Stable actor identities supported by the BBS contract."""

    HUMAN = "HUMAN"
    BOT = "BOT"


class ForumInteractionActionType(StrEnum):
    """Interaction events; every successful write appends one immutable row."""

    VIEW = "VIEW"
    LIKE = "LIKE"
    FAVORITE = "FAVORITE"
    FOLLOW = "FOLLOW"
    ACCEPT = "ACCEPT"


class ForumInteractionTargetType(StrEnum):
    """Interactive target identities."""

    TOPIC = "TOPIC"
    POST = "POST"
    HUMAN = "HUMAN"
    BOT = "BOT"


class ForumSummaryScope(StrEnum):
    """The independently valid query scope."""

    ACTOR = "ACTOR"
    TARGET = "TARGET"
    ACTOR_TARGET = "ACTOR_TARGET"


class PersonalCenterActorScope(StrEnum):
    HUMAN = "HUMAN"
    BOT = "BOT"
    ALL = "ALL"


class PersonalCenterActivityType(StrEnum):
    ALL = "ALL"
    AUTHORED = "AUTHORED"
    FAVORITED = "FAVORITED"
    VIEWED = "VIEWED"
    LIKED = "LIKED"
    REPLIED = "REPLIED"


INTERACTION_TARGET_MATRIX: dict[
    ForumInteractionActionType, frozenset[ForumInteractionTargetType]
] = {
    ForumInteractionActionType.VIEW: frozenset(
        {ForumInteractionTargetType.TOPIC, ForumInteractionTargetType.POST}
    ),
    ForumInteractionActionType.LIKE: frozenset(
        {ForumInteractionTargetType.TOPIC, ForumInteractionTargetType.POST}
    ),
    ForumInteractionActionType.FAVORITE: frozenset(
        {ForumInteractionTargetType.TOPIC, ForumInteractionTargetType.POST}
    ),
    ForumInteractionActionType.FOLLOW: frozenset(
        {
            ForumInteractionTargetType.TOPIC,
            ForumInteractionTargetType.POST,
            ForumInteractionTargetType.HUMAN,
            ForumInteractionTargetType.BOT,
        }
    ),
    ForumInteractionActionType.ACCEPT: frozenset({ForumInteractionTargetType.POST}),
}


@dataclass(frozen=True)
class ForumActorRef:
    """One actor dimension, always paired by type and stable id."""

    type: ForumActorType
    id: str


@dataclass(frozen=True)
class ForumTargetRef:
    """One target dimension, always paired by type and stable id."""

    type: ForumInteractionTargetType
    id: str


@dataclass(frozen=True)
class ForumActorTargetRef:
    """A single actor-to-target relation."""

    actor: ForumActorRef
    target: ForumTargetRef


@dataclass(frozen=True)
class ForumInteractionRecord:
    """One persisted append-only interaction event."""

    actor_type: ForumActorType
    actor_id: str
    action_type: ForumInteractionActionType
    target_type: ForumInteractionTargetType
    target_id: str
    env: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ForumStatsRecord:
    """One stable summary schema shared by actor, target and pair scopes."""

    view_count: int = 0
    like_count: int = 0
    favorite_count: int = 0
    follow_count: int = 0
    accept_count: int = 0
    topic_count: int = 0
    reply_count: int = 0
    accepted_reply_count: int = 0
    reply_participant_count: int = 0
    helpful_reply_count: int = 0


@dataclass(frozen=True)
class ForumAuthorStatsRecord:
    """Production counts for one Topic and Post author."""

    topic_count: int
    reply_count: int
    accepted_reply_count: int


@dataclass(frozen=True)
class ForumInteractionSummaryQuery:
    """One actor-only, target-only, or actor-target query.

    Each pair of fields is all present or all absent; the service validates that
    contract before persistence is called. ``None`` is therefore intentional here
    and means the dimension was not selected, not an optional domain state.
    """

    actor_type: Optional[ForumActorType] = None
    actor_id: Optional[str] = None
    target_type: Optional[ForumInteractionTargetType] = None
    target_id: Optional[str] = None


class ForumInteractionSummaryItem(ForumInteractionSummaryQuery):
    """Alias for request-array entries; array items share the same contract."""


@dataclass(frozen=True)
class ForumInteractionSummaryResult:
    """One summary result, including the selected dimensions."""

    scope: ForumSummaryScope
    actor_type: Optional[ForumActorType] = None
    actor_id: Optional[str] = None
    target_type: Optional[ForumInteractionTargetType] = None
    target_id: Optional[str] = None
    stats: ForumStatsRecord = ForumStatsRecord()


@dataclass(frozen=True)
class PersonalCenterActivityQuery:
    """One authenticated owner's activity-list query."""

    owner_user_id: str
    actor_scope: PersonalCenterActorScope
    activity_type: PersonalCenterActivityType
    target_type: Optional[ForumInteractionTargetType]
    page: int
    page_size: int


@dataclass(frozen=True)
class PersonalCenterActivityRecord:
    """One deduped activity summary row."""

    activity_type: PersonalCenterActivityType
    actor_type: ForumActorType
    actor_id: str
    target_type: ForumInteractionTargetType
    target_id: str
    topic_id: str
    happened_at: datetime
    body_preview: str
    post_id: Optional[str] = None


@dataclass(frozen=True)
class PersonalCenterActivityPage:
    """Server-paged activity rows."""

    total: int
    items: tuple[PersonalCenterActivityRecord, ...]
