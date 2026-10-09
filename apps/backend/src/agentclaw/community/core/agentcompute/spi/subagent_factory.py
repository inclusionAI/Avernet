from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentcompute.domain.models import AgentSpecRaw, Run
from agentclaw.community.core.agentcompute.spi.subagent import Subagent


@runtime_checkable
class SubagentFactory(Protocol):

    def create(self, spec: AgentSpecRaw, run: Run) -> Subagent: ...