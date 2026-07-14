"""SQLAlchemy ORM models for expert chat tables.

Single canonical definitions on the shared ``Base`` (registered so
``init_db()`` create_all() picks them up). Used by the unified
``plugins/expert_chat_repository.py`` on both the corp store and SQLite.
Column sizes / defaults / unique key mirror prod DDL — see
``specs/2026-05-18-unified-repository-round-3-session-1/
ddl-parity-ac_expert_chat_bot_sessions.md`` (sessions) and
``specs/2026-07-13-caller-instance/design.md`` (caller instance).
"""
import json

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint
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


class AcExpertChatInstance(Base):
    """ac_expert_chat_instance — per-caller baas container instance.

    Tracks a single caller's independently-provisioned container for a
    service bot, keyed by ``(bot_id, owner_id, user_id, env)``. ``ext``
    holds the baas-side association keys (``bot_uuid`` /
    ``service_bot_publish_id`` / ``version`` / ``binding_id``) as JSON;
    build artifacts (``migration_path``) stay on the publish order and
    are reverse-looked at use time, never mirrored here.

    ``status`` state machine:
    - ``init``   — instance row exists, baas container not yet active.
    - ``active`` — baas container is online and serving.
    - ``release``— baas container was recycled; next lookup resets to
      ``init`` and re-provisions.
    """

    __tablename__ = "ac_expert_chat_instance"

    # DDL: id bigint(20) AUTO_INCREMENT.
    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    bot_id = Column(String(256), nullable=True)
    owner_id = Column(String(256), nullable=True)
    user_id = Column(String(256), nullable=True)
    status = Column(String(32), nullable=True)
    ext = Column(Text, nullable=True)
    env = Column(String(32), nullable=True)
    # DDL: timestamp NULL DEFAULT CURRENT_TIMESTAMP [ON UPDATE ...].
    gmt_create = Column(DateTime, server_default=func.now(), nullable=True)
    gmt_modified = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )

    # DDL: UNIQUE KEY uk_bi_oi_ui_e(bot_id, owner_id, user_id, env) — the
    # GLOBAL modifier is an OceanBase detail; ORM side models it as a plain
    # UniqueConstraint (see design O4).
    __table_args__ = (
        UniqueConstraint(
            "bot_id",
            "owner_id",
            "user_id",
            "env",
            name="uk_bi_oi_ui_e",
        ),
    )

    def to_dict(self) -> dict:
        """Deserialize ``ext`` JSON alongside the scalar columns."""
        try:
            ext = json.loads(self.ext) if self.ext else None
        except (TypeError, ValueError):
            ext = None
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "owner_id": self.owner_id,
            "user_id": self.user_id,
            "status": self.status,
            "ext": ext,
            "env": self.env,
            "gmt_create": self.gmt_create.isoformat()
            if self.gmt_create
            else None,
            "gmt_modified": self.gmt_modified.isoformat()
            if self.gmt_modified
            else None,
        }
