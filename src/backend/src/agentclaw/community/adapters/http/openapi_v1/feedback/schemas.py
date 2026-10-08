"""Shared request and payload schemas for the feedback HTTP routes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agentclaw.community.core.feedback.models import (
    MAX_CONTENT_LENGTH,
    MAX_MODULE_LENGTH,
    MAX_REPORTER_ID_LENGTH,
)


class CreateFeedbackRequest(BaseModel):
    """Body for ``POST /openapi/v1/feedback``: submit one feedback entry.

    The reporter is declared in the body (not derived from the caller): this is
    a backend API reachable by humans, Bots and application callers alike.
    Feedback has no idempotency key — each call is a new row.
    """

    reporter_id: str = Field(
        min_length=1,
        max_length=MAX_REPORTER_ID_LENGTH,
        description="Reporter identifier (human work-no); declared by the caller.",
    )
    module: str = Field(
        min_length=1,
        max_length=MAX_MODULE_LENGTH,
        description="Product module the feedback is about, e.g. bbs / task / onboarding.",
    )
    content: str = Field(
        min_length=1,
        max_length=MAX_CONTENT_LENGTH,
        description="Feedback content. Leading and trailing whitespace is removed.",
    )


class FeedbackCreated(BaseModel):
    """Identifier of the persisted feedback entry."""

    id: int = Field(description="Stable auto-incremented feedback id.")


class FeedbackListItem(BaseModel):
    """One entry in the feedback list."""

    id: int = Field(description="Stable auto-incremented feedback id.")
    reporter_id: str = Field(description="Reporter identifier (human work-no).")
    module: str = Field(description="Product module the feedback is about.")
    content: str = Field(description="Feedback content.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(cls, record) -> "FeedbackListItem":
        return cls(
            id=record.id,
            reporter_id=record.reporter_id,
            module=record.module,
            content=record.content,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
