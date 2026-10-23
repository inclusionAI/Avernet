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
    AgentKind, AgentSpec, AgentStatus, GuardKind, ProviderKind,
    RequestMetadata, Run,
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
class BotAuthorizationDescriptor:
    """The pending human-authorization gate for one agent's bot (D1/D4).

    The shared-contract shape — the host adapter's status result and the
    run surfaces serialize the same four fields: ``state`` is the parked
    BotState value (``AWAITING_AUTHORIZATION``), ``iframe_url`` /
    ``redirect_url`` the passport application's consent surfaces as
    submitted (empty when the payload has none), ``deadline`` the
    authorization window's end as the ISO-8601 spelling the creation-job
    payload itself carries. D1: the payload is the only durable copy —
    this is a read-side join, never a second stored one. Present exactly
    for agents parked at the gate; auto-issued, provisioning and terminal
    bots surface none (D7 fast path).
    """
    state: str
    iframe_url: str
    redirect_url: str
    deadline: str


@runtime_checkable
class BotAuthorizationSource(Protocol):
    """Read-only seam over a bot's durable creation job (manifest seam).

    The service composes run surfaces from ``ac_agent_swarm_*`` rows only;
    the authorization URLs and window live in the creation-job payload the
    host platform persists, so the composition asks this seam — never the
    task queue directly (D17/D35 agentswarm isolation: the swarm's core
    module imports neither ``core.bot_management`` nor ``core.task_queue``).
    Declared with the module's own ``BotAuthorizationDescriptor`` return
    type; the concrete adapter bound by the DI root derives the same
    descriptor the host adapter reports (D1), so there is exactly one
    source of truth and zero copied state.
    """

    @abstractmethod
    def authorization(self, *, owner_id: str, bot_id: str) -> BotAuthorizationDescriptor | None:
        """The gate descriptor for a (owner, bot) creation, or ``None`` when
        no durable creation payload exists for it. Must not raise on
        infrastructure errors — callers degrade, never fail the read."""
        ...


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
class AgentAuthorizationState:
    """One bot-kind agent's current provisioning-gate snapshot (poll payload).

    The per-poll read behind the SSE gate-delta stream: every agent row that
    carries a bot state, with the ``authorization`` descriptor attached for
    those parked at the human gate. Keyed by ``agent_id``; per-bot by
    construction (D7), so a mixed run snapshots each bot independently.
    """
    agent_id: str
    name: str
    status: str
    kind: str
    spawn_round: int
    bot_id: str | None
    bot_state: str
    authorization: BotAuthorizationDescriptor | None = None


@dataclass(frozen=True)
class RunEvents:
    """Incremental durable progress: rows after the cursors plus run status."""
    agents: tuple[AgentEvent, ...]
    rounds: tuple[RoundEvent, ...]
    guards: tuple[GuardEventSummary, ...]
    run_status: RunStatus
    terminal: RunStatus | None
    agent_authorizations: tuple[AgentAuthorizationState, ...] = ()


@dataclass(frozen=True)
class AgentAttemptDetail:
    """One executor-ledger row: the binding/attempt journal of one slice retry."""
    claim_epoch: int
    slice_retry_index: int
    executor_kind: str
    executor_id: str
    executor_name: str
    binding_state: str
    result_status: str | None
    result_message: str | None
    started_at: int | None


@dataclass(frozen=True)
class AgentResultDetail:
    """The ONE final result delivered by one agent (payload or artifact spill)."""
    payload: Any | None
    artifact_key: str | None
    routed_at: int


@dataclass(frozen=True)
class AgentMessageDetail:
    """One durable per-agent message row (decision/completion/transcript body)."""
    message_id: str
    agent_id: str
    seq: int
    role: str
    content: Any | None
    decision: Any | None
    token_usage: Any | None
    artifact_key: str | None
    created_at: int


@dataclass(frozen=True)
class AgentDetailItem:
    """One agent row with its result, attempt journal and own messages."""
    agent_id: str
    name: str
    role: str
    kind: str
    status: str
    parent_agent_id: str | None
    depth: int
    spawn_round: int
    idempotency_key: str
    attempts: int
    bot_id: str | None
    bot_state: str | None
    error: str | None
    output: Any | None
    started_at: int | None
    ended_at: int | None
    result: AgentResultDetail | None
    attempt_ledger: tuple[AgentAttemptDetail, ...]
    messages: tuple[AgentMessageDetail, ...]
    authorization: BotAuthorizationDescriptor | None = None


@dataclass(frozen=True)
class RoundDetailItem:
    round_id: str
    stage_index: int
    orchestrator_steps: int
    slowest_attempt_steps: int
    cohort_agent_ids: tuple[str, ...]
    started_at: int
    ended_at: int | None


@dataclass(frozen=True)
class GuardEventDetailItem:
    event_id: str
    agent_id: str | None
    kind: str
    details: Any
    created_at: int


@dataclass(frozen=True)
class RunDetail:
    """The full durable state of one run: rows, results, journal, telemetry.

    Introspection surface for a completed (or in-flight) run — everything the
    module persisted: the run row, one detail item per agent (each with its
    ONE agent_result, its executor ledger and its own message rows), the
    round timeline, and the guard-event trail. Distinct from ``get_run``
    (a summary) and ``run_events`` (an incremental cursor read): callers get
    the complete picture in one response."""
    run_id: str
    tenant: str
    goal: str
    status: str
    termination_reason: str | None
    provider: str
    final_answer: Any
    error: str | None
    started_at: int | None
    ended_at: int | None
    agent_catalog: tuple[AgentSpec, ...]
    agents: tuple[AgentDetailItem, ...]
    rounds: tuple[RoundDetailItem, ...]
    guards: tuple[GuardEventDetailItem, ...]


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
    def get_run_detail(self, run_id: str, tenant: str) -> RunDetail | None: ...

    @abstractmethod
    async def cancel(self, run_id: str, tenant: str) -> str: ...

    @abstractmethod
    def purge_run(self, run_id: str, tenant: str) -> dict[str, int] | None: ...

    @abstractmethod
    def content_classification(self, run_id: str, tenant: str) -> dict[str, int] | None: ...
