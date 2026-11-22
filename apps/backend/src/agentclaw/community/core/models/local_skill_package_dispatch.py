"""Durable evidence that a Local Skill package request may still write its target."""

from sqlalchemy import BigInteger, Column, DateTime, Index, String, func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class LocalSkillPackageDispatch(Base):
    __tablename__ = "ac_local_skill_package_dispatch"
    __table_args__ = (
        Index("ix_local_package_dispatch_bot", "avernet_tenant", "env", "entity_id", "bot_id"),
    )

    target_key = Column(String(64), primary_key=True)
    dispatch_id = Column(String(36), nullable=False)
    avernet_tenant = Column(String(64), nullable=False)
    env = Column(String(20), nullable=False)
    entity_id = Column(String(128), nullable=False)
    bot_id = Column(String(100), nullable=False)
    binding_id = Column(BigInteger, nullable=False)
    name = Column(String(255), nullable=False)
    content_digest = Column(String(71), nullable=False)
    gmt_created = Column(DateTime, nullable=False, server_default=func.now())


register_avernet_tenant_guard(LocalSkillPackageDispatch)
