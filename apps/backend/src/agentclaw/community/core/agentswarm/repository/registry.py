"""Import-time registry of every ac_agent_swarm_* ORM model.

The old repository/models.py played this role: importing it registered all
tables on the shared Base metadata (schema.py create_all) and exposed the
model classes under one module. Importing this registry does the same for
the per-table packages.
"""
from __future__ import annotations

from agentclaw.community.core.agentswarm.repository.run.model import (
    AcAgentSwarmRunModel,
)
from agentclaw.community.core.agentswarm.repository.agent.model import (
    AcAgentSwarmAgentModel,
)
from agentclaw.community.core.agentswarm.repository.agent_attempt.model import (
    AcAgentSwarmAgentAttemptModel,
)
from agentclaw.community.core.agentswarm.repository.message.model import (
    AcAgentSwarmMessageModel,
)
from agentclaw.community.core.agentswarm.repository.agent_result.model import (
    AcAgentSwarmAgentResultModel,
)
from agentclaw.community.core.agentswarm.repository.round.model import (
    AcAgentSwarmRoundModel,
)
from agentclaw.community.core.agentswarm.repository.guard.model import (
    AcAgentSwarmGuardEventModel,
)
from agentclaw.community.core.agentswarm.repository.run_dispatch.model import (
    AcAgentSwarmRunDispatchModel,
)
from agentclaw.community.core.agentswarm.repository.audit.model import (
    AcAgentSwarmAuditModel,
)

ALL_MODELS = (
    AcAgentSwarmRunModel,
    AcAgentSwarmAgentModel,
    AcAgentSwarmAgentAttemptModel,
    AcAgentSwarmMessageModel,
    AcAgentSwarmAgentResultModel,
    AcAgentSwarmRoundModel,
    AcAgentSwarmGuardEventModel,
    AcAgentSwarmRunDispatchModel,
    AcAgentSwarmAuditModel,
)
