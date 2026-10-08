"""Domain schemas for message feedback."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BotMessageFeedbackRecord:
    """A single persisted feedback record."""

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
    env: str
    created_at: datetime
    updated_at: datetime


