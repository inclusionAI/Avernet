from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.agentswarm.domain.models import (
    Run, RunId, RunPage, RunStatus, TenantId, TerminationReason,
)


@runtime_checkable
class RunRepositoryProtocol(Protocol):

    def create(self, run: Run) -> Run: ...
    def get(self, run_id: RunId, tenant: TenantId) -> Run | None: ...
    def get_by_idempotency(self, idempotency_key: str, tenant: TenantId) -> Run | None: ...

    def update_status(self, run_id: RunId, tenant: TenantId, status: RunStatus,
                      termination_reason: TerminationReason | None = None,
                      final_answer: Any = None, error: str | None = None,
                      claim_epoch: int = 0) -> bool: ...

    # Unfenced submit-plane compensation (D28 carve-out): flips a still
    # RUNNING run to FAILED without the dispatch-epoch EXISTS fence — the
    # caller owns a run whose dispatch may never have committed. The
    # status==RUNNING predicate is the only clobber guard.
    def fail_unclaimed(self, run_id: RunId, tenant: TenantId,
                       error: str) -> bool: ...

    def list_page(self, tenant: TenantId, status: RunStatus | None = None,
                  page: int = 1, page_size: int = 20) -> RunPage: ...

    # Fleet-wide status page for the maintenance reaper (env-bound, no
    # tenant scoping) — 4.1's sweep read.
    def list_status_page(self, status: RunStatus, page: int = 1,
                         page_size: int = 100) -> RunPage: ...

    def count_active_runs(self, tenant: TenantId) -> int: ...

    def count_active_runs_global(self) -> int: ...
    def purge_run(self, run_id: RunId, tenant: TenantId) -> int: ...