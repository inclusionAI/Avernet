from __future__ import annotations

from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy import Column, Index, Integer, Text, BigInteger
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128,
)


class AcAgentSwarmRoundModel(_AgentSwarmBase, Base):
    __tablename__ = "ac_agent_swarm_round"

    round_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    stage_index = Column(Integer, nullable=False)
    orchestrator_steps = Column(Integer, nullable=False, default=0)
    slowest_attempt_steps = Column(Integer, nullable=False, default=0)
    cohort_agent_ids = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    started_at = Column(BigInteger, nullable=True)
    ended_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index(
            "uk_round",
            "env", "tenant", "run_id", "stage_index",
            unique=True,
        ),
    )