"""Service API Protocol for bot-output message feedback."""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.bot_message_feedback.schemas import BotMessageFeedbackRecord


@runtime_checkable
class BotMessageFeedbackServiceProtocol(Protocol):
    """Submit and read per-user feedback on a single bot message."""

    @abstractmethod
    async def submit_feedback(
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
        env: str | None = None,
    ) -> BotMessageFeedbackRecord:
        """Submit or overwrite feedback for a message."""
        ...

    @abstractmethod
    async def get_feedback(
        self,
        message_id: str,
        user_id: str,
        env: str | None = None,
    ) -> BotMessageFeedbackRecord | None:
        """Return this user's feedback for one message, or None when absent."""
        ...

    @abstractmethod
    async def list_feedback(
        self,
        message_ids: list[str],
        user_id: str,
        env: str | None = None,
    ) -> list[BotMessageFeedbackRecord]:
        """Return this user's feedback for the requested messages in one env.

        Missing message ids are omitted from the result.
        """
        ...
