"""`ac_agent_swarm_agent_attempt` ORM model — the per-attempt executor ledger."""
from __future__ import annotations

from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy import BigInteger, Column, Index, Integer, String, Text

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128,
)


class AcAgentSwarmAgentAttemptModel(_AgentSwarmBase, Base):
    __tablename__ = "ac_agent_swarm_agent_attempt"

    agent_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    claim_epoch = Column(BigInteger, nullable=False, default=0)
    slice_retry_index = Column(Integer, nullable=False, default=1)
    executor_kind = Column(String(16), nullable=False, default="bot")
    executor_id = Column(String(64), nullable=False)
    executor_name = Column(String(256), nullable=False)
    binding_state = Column(String(32), nullable=False, default="SPAWNED")
    result_status = Column(String(32), nullable=True)
    result_message = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    started_at = Column(BigInteger, nullable=True)
    ended_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index(
            "uk_agent_attempt",
            "env", "tenant", "run_id", "agent_id", "claim_epoch", "slice_retry_index",
            unique=True,
        ),
        Index("idx_attempt_run", "env", "tenant", "run_id"),
    )