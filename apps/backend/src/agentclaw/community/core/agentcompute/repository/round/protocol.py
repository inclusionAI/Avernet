from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentcompute.domain.models import Round, RunId, TenantId


@runtime_checkable
class RoundRepositoryProtocol(Protocol):

    def create(self, round_record: Round) -> Round: ...
    def list_by_run(self, run_id: RunId, tenant: TenantId) -> list[Round]: ...
    def purge_run(self, run_id: RunId, tenant: TenantId) -> int: ...
    def list_by_run_with_rows(self, run_id: RunId, tenant: TenantId) -> list[tuple[int, Round]]: ...