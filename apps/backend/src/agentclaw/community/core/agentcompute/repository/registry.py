"""Import-time registry of every ac_agent_compute_* ORM model.

The old repository/models.py played this role: importing it registered all
tables on the shared Base metadata (schema.py create_all) and exposed the
model classes under one module. Importing this registry does the same for
the per-table packages.
"""
from __future__ import annotations

from agentclaw.community.core.agentcompute.repository.run.model import (
    AcAgentComputeRunModel,
)
from agentclaw.community.core.agentcompute.repository.agent.model import (
    AcAgentComputeAgentModel,
)
from agentclaw.community.core.agentcompute.repository.message.model import (
    AcAgentComputeMessageModel,
)
from agentclaw.community.core.agentcompute.repository.shard.model import (
    AcAgentComputeResultShardModel,
)
from agentclaw.community.core.agentcompute.repository.round.model import (
    AcAgentComputeRoundModel,
)
from agentclaw.community.core.agentcompute.repository.guard.model import (
    AcAgentComputeGuardEventModel,
)
from agentclaw.community.core.agentcompute.repository.job.model import (
    AcAgentComputeJobModel,
)
from agentclaw.community.core.agentcompute.repository.audit.model import (
    AcAgentComputeAuditModel,
)

ALL_MODELS = (
    AcAgentComputeRunModel,
    AcAgentComputeAgentModel,
    AcAgentComputeMessageModel,
    AcAgentComputeResultShardModel,
    AcAgentComputeRoundModel,
    AcAgentComputeGuardEventModel,
    AcAgentComputeJobModel,
    AcAgentComputeAuditModel,
)
