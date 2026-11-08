from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentswarm.domain.models import Agent, AgentId, RunId, TenantId


@runtime_checkable
class AgentRepositoryProtocol(Protocol):

    def create(self, agent: Agent) -> Agent: ...
    def get(self, run_id: RunId, agent_id: AgentId, tenant: TenantId) -> Agent | None: ...
    def list_by_run(self, run_id: RunId, tenant: TenantId) -> list: ...
    def list_by_run_with_rows(self, run_id: RunId, tenant: TenantId) -> list[tuple[int, Agent]]: ...
    def update_status(self, run_id: RunId, agent_id: AgentId, tenant: TenantId,
                      status: str, attempts: int | None = None,
                      output: dict | None = None, error: str | None = None,
                      bot_id: str | None = None, bot_state: str | None = None,
                      claim_epoch: int = 0) -> bool: ...
    def purge_run(self, run_id: RunId, tenant: TenantId) -> int: ...