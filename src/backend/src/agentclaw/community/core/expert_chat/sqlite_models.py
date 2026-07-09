"""SQLAlchemy ORM model for ``ac_expert_chat_bot_sessions``.

Single canonical definition on the shared ``Base`` (registered so
``init_db()`` create_all() picks it up). Used by the unified
``plugins/expert_chat_repository.py`` on both the corp store and SQLite.
Column sizes / defaults / unique key mirror prod DDL — see
``specs/2026-05-18-unified-repository-round-3-session-1/
ddl-parity-ac_expert_chat_bot_sessions.md``.
"""
from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.plugin_api.models import AutoIncrementBigInteger, Base


class AcExpertChatBotSession(Base):
    """ac_expert_chat_bot_sessions — expert chat session binding."""

    __tablename__ = "ac_expert_chat_bot_sessions"

    # DDL: id bigint(20) AUTO_INCREMENT.
    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    user_id = Column(String(64), nullable=False)
    bot_id = Column(String(64), nullable=False)
    owner_id = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, server_default="ACTIVE")
    session_key = Column(String(255), nullable=True)
    env = Column(String(50), nullable=False)
    # DDL: timestamp NULL DEFAULT CURRENT_TIMESTAMP [ON UPDATE ...].
    gmt_create = Column(DateTime, server_default=func.now(), nullable=True)
    gmt_modified = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "bot_id",
            "owner_id",
            "env",
            name="uk_user_bot_owner_env",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
            "owner_id": self.owner_id,
            "status": self.status,
            "session_key": self.session_key,
            "env": self.env,
            "gmt_create": self.gmt_create.isoformat()
            if self.gmt_create
            else None,
            "gmt_modified": self.gmt_modified.isoformat()
            if self.gmt_modified
            else None,
        }
