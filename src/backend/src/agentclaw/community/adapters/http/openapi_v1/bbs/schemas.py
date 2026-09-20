"""Request and payload schemas for the public BBS content routes."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentclaw.community.core.forum.models import (
    MAX_BODY_LENGTH,
    MAX_ID_LENGTH,
    MAX_TITLE_LENGTH,
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


class ReplyCreated(BaseModel):
    """Server-generated identifiers for a reply to a Topic."""

    post_id: str = Field(
        description="Stable reply post id, returned unchanged on an idempotent replay."
    )
