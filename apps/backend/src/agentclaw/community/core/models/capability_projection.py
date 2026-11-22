"""Durable reconciliation obligations for a Bot's committed capabilities."""

from sqlalchemy import BigInteger, Boolean, Column, Index, String, Text
from sqlalchemy.dialects import mysql

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class BotCapabilityProjection(Base):
    __tablename__ = "ac_bot_capability_projection"
    __table_args__ = (
        Index("idx_bot_capability_projection_recovery", "env", "retry_pending", "bot_pk"),
    )

    # The Bot PK also separates a recreated Bot from any report about its predecessor.
    bot_pk = Column(BigInteger, primary_key=True, autoincrement=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    env = Column(String(20), nullable=False)
    binding_id = Column(BigInteger, nullable=True)
    device_id = Column(String(128), nullable=True)
    engine_type = Column(String(64), nullable=False)
    version = Column(BigInteger, nullable=False)
    # For runtimes without a remote ordering guarantee, persist an attempt
    # before dispatch. A newer desired-state version must not erase it.
    dispatch_version = Column(BigInteger, nullable=True)
    dispatch_device_id = Column(String(128), nullable=True)
    retry_pending = Column(Boolean, nullable=False)
    scope_json = Column(Text, nullable=False)
    retired_json = Column(Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=False)
    result_json = Column(Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=False)


register_avernet_tenant_guard(BotCapabilityProjection)
