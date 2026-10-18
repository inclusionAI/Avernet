from __future__ import annotations

from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy import Column, Index, String, Text
from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128,
)


class AcAgentSwarmGuardEventModel(_AgentSwarmBase, Base):
    __tablename__ = "ac_agent_swarm_guard_event"

    event_id = Column(_ID128, nullable=False)
    run_id = Column(_ID128, nullable=False)
    agent_id = Column(_ID128, nullable=True)
    kind = Column(String(32), nullable=False)
    details = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)

    __table_args__ = (
        Index("uk_event", "env", "tenant", "event_id", unique=True),
        Index("idx_event_run_kind", "env", "tenant", "run_id", "kind"),
    )