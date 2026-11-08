from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentResult, RunId, TenantId,
)


@runtime_checkable
class AgentResultRepositoryProtocol(Protocol):

    def create(self, result: AgentResult) -> AgentResult: ...
    def get_by_run_agent(self, run_id: RunId, agent_id: AgentId, tenant: TenantId) -> AgentResult | None: ...
    def purge_run(self, run_id: RunId, tenant: TenantId) -> int: ...
    def list_by_run(self, run_id: RunId, tenant: TenantId) -> list[AgentResult]: ...