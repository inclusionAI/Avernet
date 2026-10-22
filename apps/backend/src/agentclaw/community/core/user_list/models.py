"""Persistence model for environment-scoped frontend user-list eligibility."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.env_utils import get_current_env


class EntityUserListModel(Base):
    """One environment-scoped membership entry for one frontend feature type."""

    __tablename__ = "ac_entity_user_list"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    entity_id = Column(String(1024), nullable=False)
    subject_type = Column(String(20), nullable=False, default="user", server_default="user")
    bot_type = Column(String(20), nullable=False, default="all", server_default="all")
    user_list_type = Column(String(64), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "user_list_type",
            "env",
            "subject_type",
            "bot_type",
            name="uk_entity_user_list_scope",
        ),
        Index(
            "idx_entity_user_list_lookup",
            "env",
            "user_list_type",
            "subject_type",
            "bot_type",
            "entity_id",
        ),
    )


__all__ = ["EntityUserListModel"]
