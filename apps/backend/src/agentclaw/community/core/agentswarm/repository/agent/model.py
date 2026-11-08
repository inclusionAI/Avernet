from __future__ import annotations

from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy import Column, Index, Integer, String, Text, BigInteger
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128, _ID190,
)


class AcAgentSwarmAgentModel(_AgentSwarmBase, Base):
    __tablename__ = "ac_agent_swarm_agent"

    agent_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    parent_agent_id = Column(_ID128, nullable=True)
    depth = Column(Integer, nullable=False, default=0)
    name = Column(String(128), nullable=False)
    role = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    kind = Column(String(16), nullable=False)
    task_spec = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    spawn_round = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    idempotency_key = Column(_ID190, nullable=False)
    output = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    error = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    bot_id = Column(_ID128, nullable=True)
    bot_state = Column(String(32), nullable=True)
    started_at = Column(BigInteger, nullable=True)
    ended_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index(
            "uk_agent",
            "env", "tenant", "run_id", "agent_id",
            unique=True,
        ),
        Index(
            "uk_agent_idem",
            "env", "tenant", "run_id", "idempotency_key",
            unique=True,
        ),
        Index("idx_agent_status", "env", "tenant", "run_id", "status"),
        Index("idx_agent_parent", "env", "tenant", "run_id", "parent_agent_id"),
    )