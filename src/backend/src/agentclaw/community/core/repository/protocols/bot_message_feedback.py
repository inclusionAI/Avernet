"""Persistence contract for message feedback."""
from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_message_feedback.schemas import BotMessageFeedbackRecord


@runtime_checkable
class BotMessageFeedbackRepositoryProtocol(Protocol):
    """Persist per-user feedback on individual messages."""

    @abstractmethod
    async def upsert(
        self,
        message_id: str,
        user_id: str,
        feedback_type: str,
        message_content: str,
        user_message_id: str,
        user_message_content: str,
        bot_id: str,
        session_key: str | None = None,
        reason: str | None = None,
        comment: str | None = None,
    ) -> "BotMessageFeedbackRecord":
        """Create or update a feedback record for (message_id, user_id)."""
        ...

