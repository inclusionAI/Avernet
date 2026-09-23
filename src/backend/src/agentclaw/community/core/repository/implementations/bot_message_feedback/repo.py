"""SQLAlchemy repository for message feedback."""
from __future__ import annotations

from injector import inject

from agentclaw.community.core.bot_message_feedback.models import AcBotMessageFeedback
from agentclaw.community.core.bot_message_feedback.schemas import BotMessageFeedbackRecord
from agentclaw.community.core.repository.protocols.bot_message_feedback import (
    BotMessageFeedbackRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


class BotMessageFeedbackRepository(BotMessageFeedbackRepositoryProtocol):
    """Persists message feedback in the community database."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._model = AcBotMessageFeedback

    def _to_record(self, row: AcBotMessageFeedback) -> BotMessageFeedbackRecord:
        return BotMessageFeedbackRecord(
            id=row.id,
            message_id=row.message_id,
            message_content=row.message_content,
            user_message_id=row.user_message_id,
            user_message_content=row.user_message_content,
            session_key=row.session_key,
            bot_id=row.bot_id,
            user_id=row.user_id,
            feedback_type=row.feedback_type,
            reason=row.reason,
            comment=row.comment,
            env=row.env,
            created_at=row.gmt_create,
            updated_at=row.gmt_modified,
        )

    def _existing_row(self, session, message_id: str, user_id: str, env: str):
        return (
            session.query(self._model)
            .filter(
                self._model.message_id == message_id,
                self._model.user_id == user_id,
                self._model.env == env,
            )
            .one_or_none()
        )

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
        env: str | None = None,
    ) -> BotMessageFeedbackRecord:
        # Normalize env here as well so the repository is safe when called
        # directly; the service already supplies a non-empty value.
        env = (env or "").strip().lower() or get_current_env()

        with self._db.orm_session() as session:
            existing = self._existing_row(session, message_id, user_id, env)
            if existing is not None:
                existing.feedback_type = feedback_type
                existing.message_content = message_content
                existing.user_message_id = user_message_id
                existing.user_message_content = user_message_content
                existing.bot_id = bot_id
                if session_key is not None:
                    existing.session_key = session_key
                existing.env = env
                # 点赞时清空之前点踩的原因和评论，避免数据污脏
                if feedback_type == "like":
                    existing.reason = None
                    existing.comment = None
                else:
                    if reason is not None:
                        existing.reason = reason
                    if comment is not None:
                        existing.comment = comment
                session.flush()
                session.refresh(existing)
                return self._to_record(existing)

            row = self._model(
                message_id=message_id,
                message_content=message_content,
                user_message_id=user_message_id,
                user_message_content=user_message_content,
                user_id=user_id,
                feedback_type=feedback_type,
                session_key=session_key,
                bot_id=bot_id,
                reason=reason,
                comment=comment,
                env=env,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return self._to_record(row)
        # In the rare case of a concurrent insert that wins the unique key,
        # retry once by updating the existing row.
        # Note: the with-block above cannot catch exceptions across the close,
        # so the retry is implemented in the service layer instead.

