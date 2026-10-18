"""agentswarm Service API contract — Protocol, request DTO, typed errors.

Defined in the owning core module (api/README.md layout) so the concrete
`AgentSwarmService` (service.py, this package) can inherit its Protocol
as a core -> core import, and `api/agentswarm/` re-exports it.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.agentswarm.domain.models import (
    AgentKind, AgentSpec, AgentStatus, GuardKind, ProviderKind, RequestMetadata, Run,
    RunOptions, RunPage, RunStatus,
)


@dataclass(frozen=True)
class SubmitRunRequest:
    tenant: str
    goal: str
    agents: tuple[AgentSpec, ...]
    provider: ProviderKind = ProviderKind.DEFAULT
    request_metadata: RequestMetadata = field(default_factory=RequestMetadata)
    idempotency_key: str | None = None
    owner_id: str = ""
    run_options: RunOptions = field(default_factory=RunOptions)


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


class AgentSwarmRunNotFoundError(Exception):
    """run_events on a run id the tenant cannot see (404-class)."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run not found: {run_id}")


class AgentSwarmConfigError(Exception):
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
    dispatch behind. Protects the worker fleet (all tenants combined) from
    oversubscription; per-tenant fairness stays in TenantRunLimitExceeded.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(
            f"global max_concurrent_runs={limit} reached"
        )


class QueueDepthExceeded(Exception):
    """Pending-dispatch queue-depth cap reached (429-class, task 12.1).

    Rejects submits while the durable queue is saturated at
    ``admission.max_pending_dispatches`` — before any row is created — so a
    backlog manifests as synchronous backpressure to callers rather
    than unbounded enqueue latency.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(
            f"queue depth reached max_pending_dispatches={limit}"
        )


class AgentSwarmSyncTimeoutError(Exception):
    """submit_sync's bounded wait expired without an observable run.

    The run row is no longer visible to the tenant (deleted or purged
    mid-wait), so no real Run exists to return. submit_sync never
    fabricates a synthetic TIMED_OUT Run on expiry — the typed error
    keeps its result set to rows that were actually persisted.
    """

    def __init__(self, run_id: str, deadline: float) -> None:
        self.run_id = run_id
        self.deadline = deadline
        super().__init__(
            f"submit_sync deadline ({deadline}s) expired; "
            f"run {run_id} is no longer visible"
        )


class AgentSwarmRunNotPurgeableError(Exception):
    """purge_run refusal (task 12.9): the run is still active.

    The hard erasure is a non-transactional delete across the run's seven
    tables, so deleting a RUNNING run races an in-flight claimed
    slice's fenced writes. Only terminal runs erase; cancel or
    force-fail the run first.
    """

    def __init__(self, run_id: str, status: RunStatus) -> None:
        self.run_id = run_id
        self.status = status
        super().__init__(
            f"run {run_id} is {status.value}: "
            f"only terminal runs can be purged"
        )


@runtime_checkable
class AgentSwarmServiceProtocol(Protocol):
    """Submit/run lifecycle over the agentswarm repositories."""

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
