from __future__ import annotations

from sqlalchemy import Column, Index, Text, String, BigInteger
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128, _ID190,
)


class AcAgentSwarmRunModel(_AgentSwarmBase, Base):
    __tablename__ = "ac_agent_swarm_run"

    run_id = Column(_ID128, nullable=False)
    goal = Column(Text, nullable=False)
    agent_catalog = Column(Text, nullable=True)
    provider = Column(String(64), nullable=True)
    request_metadata = Column(Text, nullable=True)
    status = Column(String(32), nullable=False)
    termination_reason = Column(String(32), nullable=True)
    root_agent_id = Column(_ID128, nullable=True)
    final_answer = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    active_idempotency_key = Column(_ID190, nullable=True)
    started_at = Column(BigInteger, nullable=True)
    ended_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index("uk_run_id", "env", "tenant", "run_id", unique=True),
        Index("uk_run_idem", "env", "tenant", "active_idempotency_key", unique=True),
        Index("idx_run_status", "env", "tenant", "status", "gmt_modified"),
    )