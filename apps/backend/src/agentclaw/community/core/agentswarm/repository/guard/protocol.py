from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentswarm.domain.models import GuardEvent, RunId, TenantId


@runtime_checkable
class GuardEventRepositoryProtocol(Protocol):

    def create(self, event: GuardEvent) -> GuardEvent: ...
    def list_by_run(self, run_id: RunId, tenant: TenantId) -> list[GuardEvent]: ...
    def purge_run(self, run_id: RunId, tenant: TenantId) -> int: ...
    def list_by_run_with_rows(self, run_id: RunId, tenant: TenantId) -> list[tuple[int, GuardEvent]]: ...