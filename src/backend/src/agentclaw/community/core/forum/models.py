"""Immutable result records and public input limits for BBS content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_ID_LENGTH = 128
MAX_AUTHOR_TYPE_LENGTH = 16
MAX_AUTHOR_ID_LENGTH = 256
MAX_TITLE_LENGTH = 256
MAX_BODY_LENGTH = 20_000
MAX_SEARCH_KEYWORD_LENGTH = 256

AUTHOR_TYPE_BOT = "BOT"
AUTHOR_TYPE_HUMAN = "HUMAN"
AUTHOR_TYPES = frozenset({AUTHOR_TYPE_BOT, AUTHOR_TYPE_HUMAN})

TOPIC_STATUS_OPEN = "OPEN"
TOPIC_STATUS_CLOSED = "CLOSED"
TOPIC_STATUS_LOCKED = "LOCKED"
TOPIC_STATUSES = frozenset(
    {TOPIC_STATUS_OPEN, TOPIC_STATUS_CLOSED, TOPIC_STATUS_LOCKED}
)

TOPIC_TYPE_DISCUSSION = "DISCUSSION"
TOPIC_TYPE_POLL = "POLL"
TOPIC_TYPE_NOTICE = "NOTICE"
TOPIC_TYPES = frozenset({TOPIC_TYPE_DISCUSSION, TOPIC_TYPE_POLL, TOPIC_TYPE_NOTICE})


@dataclass(frozen=True)
class ForumTopicRecord:
    topic_id: str
    author_type: str
    author_id: str
    title: str
    body: str
    status: str
    created_at: datetime
    updated_at: datetime
    topic_type: str = TOPIC_TYPE_DISCUSSION


@dataclass(frozen=True)
class ForumPostRecord:
    post_id: str
    topic_id: str
    author_type: str
    author_id: str
    body: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ForumTopicPage:
    total: int
    items: tuple[ForumTopicRecord, ...]


@dataclass(frozen=True)
class ForumPostPage:
    total: int
    items: tuple[ForumPostRecord, ...]


@dataclass(frozen=True)
class ForumTopicCreateResult:
    topic: ForumTopicRecord
    created: bool


@dataclass(frozen=True)
class ForumReplyCreateResult:
    post: ForumPostRecord
    created: bool
