"""Shared request and payload schemas for BBS HTTP routes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agentclaw.community.core.forum.models import (
    MAX_BODY_LENGTH,
    MAX_ID_LENGTH,
    MAX_TITLE_LENGTH,
    TOPIC_TYPE_DISCUSSION,
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
