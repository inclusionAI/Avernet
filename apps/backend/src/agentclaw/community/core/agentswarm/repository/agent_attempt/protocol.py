from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, RunId, TenantId,
)


@runtime_checkable
class AgentAttemptRepositoryProtocol(Protocol):
    """`ac_agent_swarm_agent_attempt` — one row per attempt per slice (D-K)."""

    def record_agent_attempt_intent(self, run_id: RunId, tenant: TenantId,
                                     agent_id: AgentId, claim_epoch: int,
                                     slice_retry_index: int, executor_kind: str,
                                     executor_id: str,
                                     executor_name: str) -> bool: ...
    def record_agent_binding(self, run_id: RunId, tenant: TenantId,
                               agent_id: AgentId, claim_epoch: int, slice_retry_index: int,
                               executor_kind: str, executor_id: str,
                               executor_name: str) -> bool: ...
    def list_agent_bindings(self, run_id: RunId, tenant: TenantId) -> list[dict]: ...
    def count_agent_provisionings(self, run_id: RunId, tenant: TenantId,
                                   agent_id: AgentId) -> int: ...
    def record_attempt_result(self, run_id: RunId, tenant: TenantId,
                              agent_id: AgentId, claim_epoch: int, slice_retry_index: int,
                              result_status: str,
                              result_message: str | None) -> int: ...
    def mark_binding_destroyed(self, run_id: RunId, tenant: TenantId,
                               executor_id: str) -> int: ...
    def mark_intent_binding_destroyed(self, run_id: RunId, tenant: TenantId,
                                      agent_id: AgentId, slice_retry_index: int) -> int: ...