from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, Text
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentcompute.repository.base import (
    _AgentComputeBase, _ID128,
)


class AcAgentComputeMessageModel(_AgentComputeBase, Base):
    __tablename__ = "ac_agent_compute_message"

    message_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    agent_id = Column(_ID128, nullable=False)
    seq = Column(Integer, nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=True)
    decision = Column(Text, nullable=True)
    token_usage = Column(Text, nullable=True)
    artifact_key = Column(String(512), nullable=True)

    __table_args__ = (
        Index(
            "uk_message",
            "env", "tenant", "agent_id", "seq",
            unique=True,
        ),
        Index("idx_message_run_agent", "env", "tenant", "run_id", "agent_id"),
    )