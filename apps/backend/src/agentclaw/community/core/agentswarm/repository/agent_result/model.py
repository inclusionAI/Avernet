from __future__ import annotations

from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy import Column, Index, String, Text, BigInteger
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128,
)


class AcAgentSwarmAgentResultModel(_AgentSwarmBase, Base):
    __tablename__ = "ac_agent_swarm_agent_result"

    result_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    source_agent_id = Column(_ID128, nullable=False)
    payload = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    artifact_key = Column(String(512), nullable=True)
    routed_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index("uk_result", "env", "tenant", "result_id", unique=True),
        Index("idx_result_run", "env", "tenant", "run_id"),
        Index(
            "uk_result_agent",
            "env", "tenant", "run_id", "source_agent_id",
            unique=True,
        ),
    )