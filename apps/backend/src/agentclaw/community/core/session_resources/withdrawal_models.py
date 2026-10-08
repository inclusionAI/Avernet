"""Additive outbox; accepted means durable ECB intake, not completed withdrawal."""

from dataclasses import fields

from sqlalchemy import Column, DateTime, Index, Integer, String, func

from agentclaw.community.core.base import Base
from agentclaw.community.core.session_resources.withdrawal_types import (
    ResourceWithdrawal,
)


class ResourceWithdrawalModel(Base):
    __tablename__ = "ac_tc_resource_withdrawal"

    event_id = Column(String(160), primary_key=True)
    res_id = Column(String(128), nullable=False, unique=True)
    tenant = Column(String(128), nullable=False)
    status = Column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    available_at = Column(DateTime, nullable=False, server_default=func.now())
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())
    lease_token = Column(
        String(32),
        nullable=True,
    )
    lease_until = Column(DateTime, nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    last_error = Column(String(64), nullable=False, default="", server_default="")
    replay_actor = Column(String(128), nullable=False, default="", server_default="")
    replay_reason = Column(String(512), nullable=False, default="", server_default="")
    __table_args__ = (
        Index("ix_tc_withdrawal_due", "tenant", "status", "available_at"),
        Index("ix_tc_withdrawal_lease", "tenant", "status", "lease_until"),
    )

    def to_record(self) -> ResourceWithdrawal:
        return ResourceWithdrawal(
            **{f.name: getattr(self, f.name) for f in fields(ResourceWithdrawal)}
        )
