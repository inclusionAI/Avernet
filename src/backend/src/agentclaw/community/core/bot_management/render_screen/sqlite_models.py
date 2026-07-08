"""Render screen ORM model for SQLite auto-table-creation."""
from sqlalchemy import Column, DateTime, Index, Integer, String
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.env_utils import get_current_env


class RenderScreenModel(Base):
    """SQLAlchemy ORM model for ac_bot_render_screen table."""
    __tablename__ = "ac_bot_render_screen"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(String(64), nullable=False)
    owner_id = Column(String(64), nullable=False)
    name = Column(String(256), nullable=False)
    cdn_url = Column(String(1024), nullable=False)
    env = Column(String(16), default=get_current_env, nullable=False)
    creator_id = Column(String(64), nullable=False)
    is_delete = Column(Integer, nullable=False, default=0)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Production ac_bot_render_screen has NO unique index on
    # (bot_id, name, env) — see specs/2026-05-17-unified-repository-round-2/
    # ddl-parity-ac_bot_render_screen.md. The model is aligned to prod
    # (no unique constraint); duplicate-name rejection is enforced at the
    # service layer (RenderScreenService.create_render_screen), which is
    # what actually runs in production.
    __table_args__ = (
        Index(
            "idx_bot_id_env_del",
            "bot_id",
            "env",
            "is_delete",
        ),
    )
