"""ORM model for bot-output message feedback."""
from __future__ import annotations

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint, func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger


class AcBotMessageFeedback(Base):
    """Feedback left by a user on a single bot output message."""

    __tablename__ = "ac_bot_message_feedback"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    message_id = Column(String(128), nullable=False)
    message_content = Column(Text, nullable=False)
    user_message_id = Column(String(128), nullable=False)
    user_message_content = Column(Text, nullable=False)
    session_key = Column(String(256), nullable=True)
    bot_id = Column(String(128), nullable=False)
    user_id = Column(String(128), nullable=False)
    feedback_type = Column(String(16), nullable=False)
    reason = Column(Text, nullable=True)
    comment = Column(Text, nullable=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "user_id",
            name="uk_ac_bot_message_feedback_msg_user",
        ),
    )
