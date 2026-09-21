"""Message feedback service implementation."""
from __future__ import annotations

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_message_feedback.schemas import BotMessageFeedbackRecord
from agentclaw.community.core.bot_message_feedback.service_protocol import (
    BotMessageFeedbackServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot_message_feedback import (
    BotMessageFeedbackRepositoryProtocol,
)


class BotMessageFeedbackService(BotMessageFeedbackServiceProtocol):
    """Business logic for submitting message feedback."""

    VALID_FEEDBACK_TYPES = {"like", "dislike"}

    @inject
    def __init__(self, repo: BotMessageFeedbackRepositoryProtocol) -> None:
        self._repo = repo

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
    ) -> BotMessageFeedbackRecord:
        if not message_id or not message_id.strip():
            raise ValueError("message_id is required")
        if not user_id or not user_id.strip():
            raise ValueError("user_id is required")

        feedback_type = feedback_type.lower()
        if feedback_type not in self.VALID_FEEDBACK_TYPES:
            raise ValueError(
                f"feedback_type must be one of {sorted(self.VALID_FEEDBACK_TYPES)}"
            )

        if comment is not None and not isinstance(comment, str):
            raise ValueError("comment must be a string or null")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("reason must be a string or null")

        # 点赞时不保留之前点踩的原因/评论，避免数据污脏
        if feedback_type == "like":
            reason = None
            comment = None

        try:
            return await self._repo.upsert(
                message_id=message_id,
                user_id=user_id,
                feedback_type=feedback_type,
                message_content=message_content,
                user_message_id=user_message_id,
                user_message_content=user_message_content,
                session_key=session_key,
                bot_id=bot_id,
                reason=reason,
                comment=comment,
            )
        except IntegrityError:
            # Concurrent insert: the unique key already exists, retry as update.
            return await self._repo.upsert(
                message_id=message_id,
                user_id=user_id,
                feedback_type=feedback_type,
                message_content=message_content,
                user_message_id=user_message_id,
                user_message_content=user_message_content,
                session_key=session_key,
                bot_id=bot_id,
                reason=reason,
                comment=comment,
            )

