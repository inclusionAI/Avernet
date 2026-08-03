"""Durable, Bot-scoped cleanup work for obsolete Local Skill packages."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint, func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger


class LocalSkillCleanupWorkModel(Base):
    """An internal retry record for one obsolete package locator.

    The record is strictly scoped by the deployment-wide unique
    ``(env, owner_id, bot_id)`` Bot identity.  It is not a tenant catalog.
    """

    __tablename__ = "ac_local_skill_cleanup_work"
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    env = Column(String(20), nullable=False)
    owner_id = Column(String(128), nullable=False)
    bot_id = Column(String(100), nullable=False)
    skill_id = Column(Integer, nullable=False)
    package_locator = Column(String(1024), nullable=False)
    package_locator_hash = Column(String(64), nullable=False)
    requires_runtime_restore = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    cleaned_at = Column(DateTime, nullable=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint(
            "env", "owner_id", "bot_id", "package_locator_hash",
            name="uk_local_skill_cleanup_scope_locator_hash",
        ),
        Index("idx_local_skill_cleanup_pending", "env", "status", "gmt_create"),
    )
