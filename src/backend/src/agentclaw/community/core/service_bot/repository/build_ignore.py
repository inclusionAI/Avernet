"""Tenant-guarded build configuration; JSON keeps literal paths lossless."""

from sqlalchemy import Column, DateTime, Integer, JSON, String, UniqueConstraint
from sqlalchemy.sql import func
from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class BuildIgnoreModel(Base):
    __tablename__ = "ac_bot_build_ignore"
    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(64), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    env = Column(String(20), nullable=False)
    entity_id = Column(String(1024), nullable=False)
    bot_id = Column(String(256), nullable=False)
    engine_type = Column(String(64), nullable=False)
    paths = Column(JSON, nullable=False)
    revision = Column(Integer, nullable=False)
    modifier = Column(String(1024), nullable=False)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )
    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant", "config_key", name="uk_build_ignore_tenant_key"
        ),
    )


register_avernet_tenant_guard(BuildIgnoreModel)
