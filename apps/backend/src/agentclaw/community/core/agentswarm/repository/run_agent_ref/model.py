from __future__ import annotations

from sqlalchemy import BigInteger, Column, Index

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128,
)


class AcAgentSwarmRunAgentRefModel(_AgentSwarmBase, Base):
    """Which definition version one catalog entry of one run came from.

    The row relates to the **catalog entry**, not to a spawned agent instance:
    ``catalog_name`` joins to ``AgentSpec.name`` inside
    ``ac_agent_swarm_run.agent_catalog``. One entry may spawn several agents
    (each named ``{catalog_name}_{agent_id[:12]}``), and they all share this one
    provenance record. Keying on the instance instead would duplicate the same
    fact per spawn and would lose provenance entirely for runs that never
    spawned.

    Nothing here is added to the catalog snapshot itself, which is what keeps
    run snapshots readable by releases that predate agent definitions.
    """

    __tablename__ = "ac_agent_swarm_run_agent_ref"

    run_id = Column(_ID128, nullable=False)
    catalog_name = Column(_ID128, nullable=False)
    definition_id = Column(_ID128, nullable=False)
    # The `ac_agent_swarm_agent_definition.id` this entry resolved to - the
    # version handle, since the definition table has no revision column.
    definition_row_id = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index(
            "uk_run_catalog",
            "env", "tenant", "run_id", "catalog_name",
            unique=True,
        ),
        Index("idx_prov_run", "env", "tenant", "run_id"),
    )