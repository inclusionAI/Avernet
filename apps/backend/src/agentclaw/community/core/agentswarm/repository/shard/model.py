from __future__ import annotations

from sqlalchemy import Column, Index, String, Text, BigInteger
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128,
)


class AcAgentSwarmResultShardModel(_AgentSwarmBase, Base):
    __tablename__ = "ac_agent_swarm_result_shard"

    shard_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    source_agent_id = Column(_ID128, nullable=False)
    payload = Column(Text, nullable=True)
    artifact_key = Column(String(512), nullable=True)
    routed_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index("uk_shard", "env", "tenant", "shard_id", unique=True),
        Index("idx_shard_run", "env", "tenant", "run_id"),
        Index(
            "uk_shard_agent",
            "env", "tenant", "run_id", "source_agent_id",
            unique=True,
        ),
    )