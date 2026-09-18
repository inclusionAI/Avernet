"""Persistence model for Bot execution identity bindings."""

from sqlalchemy import BigInteger, Column, DateTime, Index, String
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard
from agentclaw.community.utils.env_utils import get_current_env


class BotExecutionIdentityBindingModel(Base):
    __tablename__ = "ac_bot_execution_identity_binding"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    bot_pk = Column(BigInteger, nullable=False, comment="ac_bots.id")
    execution_workno = Column(String(1024), nullable=False)
    identity_type = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False)
    authorization_id = Column(String(256), nullable=True)
    credential_id = Column(String(256), nullable=True)
    agent_id = Column(String(1200), nullable=True)
    credential_status = Column(String(32), nullable=True)
    failure_reason = Column(String(1024), nullable=True)
    modifier_id = Column(String(1024), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "idx_bot_execution_identity_state",
            "bot_pk",
            "env",
            "status",
        ),
    )


register_avernet_tenant_guard(BotExecutionIdentityBindingModel)

__all__ = ["BotExecutionIdentityBindingModel"]
