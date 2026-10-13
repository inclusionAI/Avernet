from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, String, Text
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128,
)


class AcAgentSwarmJobModel(_AgentSwarmBase, Base):
    __tablename__ = "ac_agent_swarm_job"

    job_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    status = Column(String(20), nullable=False)
    run_at = Column(DateTime, nullable=False)
    claimed_by = Column(String(128), nullable=True)
    claim_epoch = Column(BigInteger, nullable=False, default=0)
    lease_expires_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    reclaims = Column(Integer, nullable=False, default=0)
    deadline_at = Column(DateTime, nullable=False)
    last_error = Column(Text, nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False, server_default="0")

    __table_args__ = (
        Index("uk_job_run", "env", "tenant", "run_id", unique=True),
        Index("idx_job_scope_status_run_at", "env", "tenant", "status", "run_at"),
        Index("idx_job_scope_lease", "env", "tenant", "lease_expires_at"),
    )