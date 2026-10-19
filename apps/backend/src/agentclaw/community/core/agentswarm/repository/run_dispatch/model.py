from __future__ import annotations

from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, String, Text
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128,
)


class AcAgentSwarmRunDispatchModel(_AgentSwarmBase, Base):
    __tablename__ = "ac_agent_swarm_run_dispatch"

    dispatch_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    status = Column(String(20), nullable=False)
    ready_at = Column(DateTime, nullable=False)
    claimed_by = Column(String(128), nullable=True)
    claim_epoch = Column(BigInteger, nullable=False, default=0)
    lease_expires_at = Column(DateTime, nullable=True)
    claims = Column(Integer, nullable=False, default=0)
    reclaims = Column(Integer, nullable=False, default=0)
    progress_marker = Column(
        BigInteger, nullable=False, default=-1, server_default="-1")
    deadline_at = Column(DateTime, nullable=False)
    last_error = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False, server_default="0")

    __table_args__ = (
        Index("uk_dispatch_run", "env", "tenant", "run_id", unique=True),
        Index("idx_dispatch_scope_status_ready_at", "env", "tenant", "status", "ready_at"),
        Index("idx_dispatch_scope_lease", "env", "tenant", "lease_expires_at"),
    )