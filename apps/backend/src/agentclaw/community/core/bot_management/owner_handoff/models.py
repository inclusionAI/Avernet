"""Operation journal; credentials and confirmation URLs are never persisted."""

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class OwnerHandoffModel(Base):
    __tablename__ = "ac_bot_owner_handoff"
    operation_id = Column(String(32), primary_key=True)
    bot_pk = Column(Integer, nullable=False)
    bot_id = Column(String(256), nullable=False)
    entity_id = Column(String(1024), nullable=False)
    old_owner_id = Column(String(1024), nullable=False)
    new_owner_id = Column(String(1024), nullable=False)
    new_owner_name = Column(String(256), nullable=False)
    actor_id = Column(String(1024), nullable=False)
    request_key = Column(String(64), nullable=False)
    phase = Column(String(32), nullable=False)
    execution_workno = Column(String(1024), nullable=False)
    binding_id = Column(Integer, nullable=True)
    old_credential_id = Column(String(256), nullable=True)
    credential_id = Column(String(256), nullable=True)
    authorization_id = Column(String(256), nullable=True)
    agent_id = Column(String(1200), nullable=True)
    error_code = Column(String(64), nullable=True)
    worker = Column(String(32), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    env = Column(String(20), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "bot_pk",
            "request_key",
            name="uk_bot_owner_handoff_request",
        ),
    )


register_avernet_tenant_guard(OwnerHandoffModel)
