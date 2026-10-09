"""agentcompute Service API contract — Protocol, request DTO, typed errors.

Defined in the owning core module (api/README.md layout) so the concrete
`AgentComputeService` (service.py, this package) can inherit its Protocol
as a core -> core import, and `api/agentcompute_service.py` re-exports it.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentcompute.domain.models import (
    AgentKind, AgentSpecRaw, AgentStatus, GuardKind, RequestMetadata, Run,
    RunPage, RunStatus,
)


@dataclass(frozen=True)
class SubmitRunRequest:
    tenant: str
    goal: str
    agents: tuple[AgentSpecRaw, ...]
    provider: str = "default"
    model: str | None = None
    request_metadata: RequestMetadata = field(default_factory=RequestMetadata)
    idempotency_key: str | None = None


@dataclass(frozen=True)
class AgentEvent:
    """One durable agent row reflected to SSE consumers (spec 7.4)."""
    agent_id: str
    name: str
    status: AgentStatus
    kind: AgentKind
    spawn_round: int
    row_cursor: int


@dataclass(frozen=True)
class RoundEvent:
    round_id: str
    stage_index: int
    cohort_size: int
    row_cursor: int


@dataclass(frozen=True)
class GuardEventSummary:
    kind: GuardKind
    row_cursor: int


@dataclass(frozen=True)
class RunEvents:
    """Incremental durable progress: rows after the cursors plus run status."""
    agents: tuple[AgentEvent, ...]
    rounds: tuple[RoundEvent, ...]
    guards: tuple[GuardEventSummary, ...]
    run_status: RunStatus
    terminal: RunStatus | None


class AgentComputeRunNotFoundError(Exception):
    """run_events on a run id the tenant cannot see (404-class)."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run not found: {run_id}")


class AgentComputeConfigError(Exception):
    """Fail-clear submit refusal: the LLM config is missing/empty (400-class)."""


class TenantRunLimitExceeded(Exception):
    """Per-tenant concurrency cap reached (429-class).

    Tenant identity comes from the authenticated principal at the
    boundary (D25); one tenant hitting its limit never refuses another.
    """

    def __init__(self, tenant: str, limit: int) -> None:
        self.tenant = tenant
        self.limit = limit
        super().__init__(
            f"tenant {tenant} reached max_concurrent_runs={limit}"
        )


class GlobalRunLimitExceeded(Exception):
    """Fleet-wide concurrency cap reached (429-class, task 12.1).

    Raised from submit before any row is created: admission is strictly
    accept-or-reject, so a rejected submit leaves no run and no durable
    job behind. Protects the worker fleet (all tenants combined) from
    oversubscription; per-tenant fairness stays in TenantRunLimitExceeded.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(
            f"global max_concurrent_runs={limit} reached"
        )


class QueueDepthExceeded(Exception):
    """Pending-job queue-depth cap reached (429-class, task 12.1).

    Rejects submits while the durable queue is saturated at
    ``admission.max_pending_jobs`` — before any row is created — so a
    backlog manifests as synchronous backpressure to callers rather
    than unbounded enqueue latency.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(
            f"queue depth reached max_pending_jobs={limit}"
        )


@runtime_checkable
class AgentComputeServiceProtocol(Protocol):
    """Submit/run lifecycle over the agentcompute repositories."""

    @abstractmethod
    async def submit_async(self, req: SubmitRunRequest) -> str: ...

    @abstractmethod
    async def submit_sync(self, req: SubmitRunRequest) -> Run: ...

    @abstractmethod
    def get_run(self, run_id: str, tenant: str) -> Run | None: ...

    @abstractmethod
    def list_runs(self, tenant: str, status: RunStatus | None = None,
                  *, page: int = 1, page_size: int = 20) -> RunPage: ...

    @abstractmethod
    def run_events(self, run_id: str, tenant: str, after_agent_id: int = 0,
                   after_round_id: int = 0, after_event: int = 0) -> RunEvents: ...

    @abstractmethod
    async def cancel(self, run_id: str, tenant: str) -> str: ...

    @abstractmethod
    def purge_run(self, run_id: str, tenant: str) -> dict[str, int] | None: ...

    @abstractmethod
    def content_classification(self, run_id: str, tenant: str) -> dict[str, int] | None: ...