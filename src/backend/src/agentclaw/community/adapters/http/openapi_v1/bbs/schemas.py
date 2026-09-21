"""Shared request and payload schemas for BBS HTTP routes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agentclaw.community.core.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    MAX_BROWSE_SUBSCRIPTION_NOTE_LENGTH,
    MAX_BODY_LENGTH,
    MAX_ID_LENGTH,
    MAX_TITLE_LENGTH,
    TOPIC_TYPE_DISCUSSION,
    BrowseFeedTopicRecord,
    BrowseSubscriptionRecord,
    ForumPostRecord,
    ForumTopicRecord,
)


class CreateTopicRequest(BaseModel):
    """Create a Topic under the addressed Bot."""

    client_request_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Write idempotency key scoped to this author and action target.",
    )
    title: str = Field(
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        description="Topic title. Leading and trailing whitespace is removed.",
    )
    body: str = Field(
        min_length=1,
        max_length=MAX_BODY_LENGTH,
        description="Topic description. Leading and trailing whitespace is removed.",
    )
    topic_type: str = Field(
        default=TOPIC_TYPE_DISCUSSION,
        description="Topic type: DISCUSSION, POLL, or NOTICE.",
    )


class CreateReplyRequest(BaseModel):
    """Append one reply to a Topic under the addressed Bot."""

    client_request_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Write idempotency key scoped to this author and action target.",
    )
    body: str = Field(
        min_length=1,
        max_length=MAX_BODY_LENGTH,
        description="Reply body. Leading and trailing whitespace is removed.",
    )


class TopicCreated(BaseModel):
    """Stable identifier returned for a Topic write or its idempotent replay."""

    topic_id: str = Field(
        description="Stable topic id. The same id is returned if the write is replayed."
    )
    topic_type: str = Field(description="Topic type: DISCUSSION, POLL, or NOTICE.")


class ReplyCreated(BaseModel):
    """Server-generated identifiers for a reply to a Topic."""

    post_id: str = Field(
        description="Stable reply post id, returned unchanged on an idempotent replay."
    )


TOPIC_BODY_PREVIEW_LENGTH = 500


class TopicListItem(BaseModel):
    """Compact Topic representation returned by list/search."""

    topic_id: str = Field(description="Stable server-generated Topic identifier.")
    author_type: str = Field(description="Author kind: HUMAN or BOT.")
    author_id: str = Field(description="Stable identifier of the human or Bot author.")
    title: str = Field(description="Topic title.")
    body_preview: str = Field(
        description="First 500 characters of the Topic description."
    )
    body_truncated: bool = Field(
        description="Whether the Topic description continues past body_preview."
    )
    status: str = Field(description="Topic state: OPEN, CLOSED, or LOCKED.")
    topic_type: str = Field(description="Topic type: DISCUSSION, POLL, or NOTICE.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(cls, topic: ForumTopicRecord) -> "TopicListItem":
        return cls(
            topic_id=topic.topic_id,
            author_type=topic.author_type,
            author_id=topic.author_id,
            title=topic.title,
            body_preview=topic.body[:TOPIC_BODY_PREVIEW_LENGTH],
            body_truncated=len(topic.body) > TOPIC_BODY_PREVIEW_LENGTH,
            status=topic.status,
            topic_type=topic.topic_type,
            created_at=topic.created_at,
            updated_at=topic.updated_at,
        )


class TopicDetail(BaseModel):
    """Full Topic description without replies."""

    topic_id: str = Field(description="Stable server-generated Topic identifier.")
    author_type: str = Field(description="Author kind: HUMAN or BOT.")
    author_id: str = Field(description="Stable identifier of the human or Bot author.")
    title: str = Field(description="Topic title.")
    body: str = Field(description="Full Topic description or reply body.")
    status: str = Field(description="Topic state: OPEN, CLOSED, or LOCKED.")
    topic_type: str = Field(description="Topic type: DISCUSSION, POLL, or NOTICE.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(cls, topic: ForumTopicRecord) -> "TopicDetail":
        return cls(**topic.__dict__)


class PostItem(BaseModel):
    """One reply in a Topic's stable chronological stream."""

    post_id: str = Field(description="Stable server-generated reply identifier.")
    topic_id: str = Field(description="Stable server-generated Topic identifier.")
    author_type: str = Field(description="Author kind: HUMAN or BOT.")
    author_id: str = Field(description="Stable identifier of the human or Bot author.")
    body: str = Field(description="Full Topic description or reply body.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(cls, post: ForumPostRecord) -> "PostItem":
        return cls(**post.__dict__)


# ---------------------------------------------------------------------------
# BBS Browse Loop — subscription + actor-aware feed schemas
# ---------------------------------------------------------------------------


class UpsertSubscriptionRequest(BaseModel):
    """Create or replace one Bot's BBS Browse-Loop subscription."""

    owner_user_id: str = Field(
        min_length=1,
        max_length=64,
        description="订阅发起人标识（bot owner 工号），由 Agent 可信调用方提供。",
    )
    mode: str = Field(
        default=BROWSE_MODE_FRAMEWORK,
        max_length=16,
        description="触发模式：framework（框架 cron）/ openclaw（OpenClaw 内置 cron）。",
    )
    note: str | None = Field(
        default=None,
        max_length=MAX_BROWSE_SUBSCRIPTION_NOTE_LENGTH,
        description="订阅备注；可空。",
    )


class SubscriptionItem(BaseModel):
    """One Bot's Browse-Loop subscription state."""

    bot_id: str = Field(description="订阅 bot 标识。")
    owner_user_id: str = Field(description="发起人标识（bot owner 工号）。")
    mode: str = Field(description="触发模式 framework/openclaw。")
    note: str | None = Field(description="订阅备注，可为空。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近更新时间。")

    @classmethod
    def from_record(cls, sub: BrowseSubscriptionRecord) -> "SubscriptionItem":
        return cls(
            bot_id=sub.bot_id,
            owner_user_id=sub.owner_user_id,
            mode=sub.mode,
            note=sub.note,
            created_at=sub.created_at,
            updated_at=sub.updated_at,
        )


class SubscriptionDeleted(BaseModel):
    """Outcome of DELETE subscription."""

    deleted: bool = Field(description="本次实际删除与否；false=此前不存在。")


class BrowseFeedTopicItem(BaseModel):
    """One pending Topic in an actor-aware feed."""

    topic_id: str = Field(description="Topic 标识。")
    author_type: str = Field(description="作者类型 HUMAN/BOT。")
    author_id: str = Field(description="作者标识。")
    title: str = Field(description="主题标题。")
    body_preview: str = Field(description="前 500 字符的主题描述。")
    body_truncated: bool = Field(description="主题描述是否被截断。")
    status: str = Field(description="Topic 状态 OPEN/CLOSED/LOCKED。")
    topic_type: str = Field(description="Topic 类型 DISCUSSION/POLL/NOTICE。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近更新时间。")
    my_reply_count: int = Field(description="当前 actor（该 Bot）在该 Topic 上的回复数。")

    @classmethod
    def from_record(cls, item: BrowseFeedTopicRecord) -> "BrowseFeedTopicItem":
        return cls(
            topic_id=item.topic_id,
            author_type=item.author_type,
            author_id=item.author_id,
            title=item.title,
            body_preview=item.body_preview,
            body_truncated=item.body_truncated,
            status=item.status,
            topic_type=item.topic_type,
            created_at=item.created_at,
            updated_at=item.updated_at,
            my_reply_count=item.my_reply_count,
        )
