from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, Message, RunId, TenantId,
)


@runtime_checkable
class MessageRepositoryProtocol(Protocol):

    def create(self, message: Message) -> Message: ...
    def list_by_agent(self, run_id: RunId, agent_id: AgentId, tenant: TenantId) -> list[Message]: ...
    def apply_retention(self, run_id: RunId, agent_id: AgentId, tenant: TenantId,
                        tail: int) -> int: ...
    def purge_run(self, run_id: RunId, tenant: TenantId) -> int: ...