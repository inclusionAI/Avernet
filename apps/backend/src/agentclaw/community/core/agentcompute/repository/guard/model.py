from __future__ import annotations

from sqlalchemy import Column, Index, String, Text
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentcompute.repository.base import (
    _AgentComputeBase, _ID128,
)


class AcAgentComputeGuardEventModel(_AgentComputeBase, Base):
    __tablename__ = "ac_agent_compute_guard_event"

    event_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    agent_id = Column(_ID128, nullable=True)
    kind = Column(String(32), nullable=False)
    details = Column(Text, nullable=True)

    __table_args__ = (
        Index("uk_event", "env", "tenant", "event_id", unique=True),
        Index("idx_event_run_kind", "env", "tenant", "run_id", "kind"),
    )