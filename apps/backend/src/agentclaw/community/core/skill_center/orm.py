"""SQLAlchemy ORM models owned by the skill_center domain.

Relocated out of ``plugins/local/sqlite_models.py``. Both map real tables
with genuine unique indexes on every runtime; the old module path implied
local-only, which they never were.
"""
from sqlalchemy import Column, Integer, String, DateTime, Index, func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class DefaultSkillsetMcpExclusion(Base):
    """用户从默认能力集中排除的 MCP 记录。

    此表仅用于 is_default=1 的能力集，禁止用于普通能力集。
    """
    __tablename__ = "ac_default_skillset_mcp_exclusion"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    bot_id = Column(String(64), nullable=False, index=True)
    skill_set_id = Column(Integer, nullable=False, index=True)
    server_code = Column(String(255), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    excluded_at = Column(DateTime, nullable=False, default=func.now())
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uk_user_bot_skillset_mcp", "avernet_tenant", "user_id", "bot_id", "skill_set_id", "server_code", unique=True),
        {"extend_existing": True},
    )


class DefaultSkillsetSkillExclusion(Base):
    """用户从默认技能集中排除的 Skill 记录。

    此表仅用于 is_default=1 的技能集，禁止用于普通技能集。
    """
    __tablename__ = "ac_default_skillset_skill_exclusion"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    bot_id = Column(String(64), nullable=False, index=True)
    skill_set_id = Column(Integer, nullable=False, index=True)
    skill_id = Column(Integer, nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    excluded_at = Column(DateTime, nullable=False, default=func.now())
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uk_user_bot_skillset_skill", "avernet_tenant", "user_id", "bot_id", "skill_set_id", "skill_id", unique=True),
        {"extend_existing": True},
    )


register_avernet_tenant_guard(DefaultSkillsetMcpExclusion)
register_avernet_tenant_guard(DefaultSkillsetSkillExclusion)
