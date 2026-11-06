"""``ac_agent_swarm_identity`` — a run's one AgentPass, durable across restarts.

The registry that holds this in memory is necessary but not sufficient: a process
restart would empty it, and the deterministic run-derived bot id would then
re-apply against an identity the service already issued. That is wasted work at
best and a second identity at worst, depending on the provider.

One row per run. The identity is addressed by ``bot_id``, so the row records the
bot id the run minted and the agent code that came back with it — the two things
a restarted process needs to resume rather than re-mint.

``worker_count`` is not a refcount and does not gate destruction: it records how
many workers the run bound, so an operator can see a run's shape and a teardown
can tell a live run from an abandoned one.
"""
from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase,
    _ID128,
    _ID190,
)
from agentclaw.community.core.base import Base


class AcAgentSwarmIdentityModel(_AgentSwarmBase, Base):
    __tablename__ = "ac_agent_swarm_identity"

    #: The run this identity belongs to — the registry's key, and the join a
    #: restarted process resumes on.
    run_id = Column(_ID128, nullable=False)

    #: The swarm bot the run minted. The AgentPass is addressed by this id, so it
    #: is the row's identity even though ``run_id`` is its key.
    swarm_bot_id = Column(_ID190, nullable=False)

    #: What the service returned for the single application. The token is never
    #: stored — it is fetched on demand, the same rule every other Passport
    #: surface follows.
    agent_code = Column(_ID190, nullable=False)
    status = Column(String(32), nullable=False, default="ISSUED")

    #: The run's accumulated resource scope, as the union of what its personas
    #: declared. Stored so a restart does not have to re-derive it from workers
    #: that may already be gone.
    mcp_codes = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    cli_items = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)

    #: How many workers the run has bound to this identity.
    worker_count = Column(Integer, nullable=False, default=0)

    owner_id = Column(String(64), nullable=False, default="")

    __table_args__ = (
        Index("uk_swarm_identity_run", "env", "tenant", "run_id", unique=True),
        Index("idx_swarm_identity_bot", "env", "tenant", "swarm_bot_id"),
        Index("idx_swarm_identity_status", "env", "tenant", "status", "gmt_modified"),
    )