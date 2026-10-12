from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentcompute.domain.models import (
    AgentId, ResultShard, RunId, TenantId,
)


@runtime_checkable
class ResultShardRepositoryProtocol(Protocol):

    def create(self, shard: ResultShard) -> ResultShard: ...
    def get_by_run_agent(self, run_id: RunId, agent_id: AgentId, tenant: TenantId) -> ResultShard | None: ...
    def purge_run(self, run_id: RunId, tenant: TenantId) -> int: ...
    def list_by_run(self, run_id: RunId, tenant: TenantId) -> list[ResultShard]: ...