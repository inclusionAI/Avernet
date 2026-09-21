"""ORM model for bot-output message feedback."""
from __future__ import annotations

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint, func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.env_utils import get_current_env


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
    env = Column(
        String(32),
        nullable=False,
        default=get_current_env,
        comment="Environment: prod/pre/dev",
    )
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Scope the unique key by env so feedback is isolated per environment.
        # A user can leave one feedback per (message, environment); changing env
        # creates a new record instead of overwriting the previous one.
        UniqueConstraint(
            "message_id",
            "user_id",
            "env",
            name="uk_ac_bot_message_feedback_msg_user_env",
        ),
    )
