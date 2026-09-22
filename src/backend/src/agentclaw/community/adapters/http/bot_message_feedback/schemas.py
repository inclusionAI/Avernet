"""HTTP schemas for message feedback."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SubmitFeedbackRequest(BaseModel):
    """Request body for submitting feedback on a bot message."""

    feedback_type: Literal["like", "dislike"] = Field(
        ...,
        description="Feedback type: like or dislike",
    )
    message_content: str = Field(
        ...,
        max_length=65535,
        description="Content of the message being rated",
    )
    user_message_id: str = Field(
        ...,
        max_length=128,
        description="ID of the paired user question message",
    )
    user_message_content: str = Field(
        ...,
        max_length=65535,
        description="Content of the paired user question message",
    )
    session_key: str | None = Field(
        None,
        description="Optional session key the message belongs to",
    )
    bot_id: str = Field(
        ...,
        max_length=128,
        description="Bot ID the message belongs to",
    )
    reason: str | None = Field(
        None,
        max_length=2000,
        description="Optional reason(s) for the feedback",
    )
    comment: str | None = Field(
        None,
        max_length=2000,
        description="Optional free-text comment",
    )


class BotMessageFeedbackResponse(BaseModel):
    """A single feedback record returned to callers."""

    id: int
    message_id: str
    message_content: str
    user_message_id: str
    user_message_content: str
    session_key: str | None
    bot_id: str
    user_id: str
    feedback_type: str
    reason: str | None
    comment: str | None
    created_at: datetime
    updated_at: datetime


