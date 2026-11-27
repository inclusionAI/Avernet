"""agentswarm Service API contract — Protocol, request DTO, typed errors.

Defined in the owning core module (api/README.md layout) so the concrete
`AgentSwarmService` (service.py, this package) can inherit its Protocol
as a core -> core import, and `api/agentswarm/` re-exports it.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

from agentclaw.community.core.agentswarm.domain.models import (
    AgentDefinition, AgentDefinitionId, AgentDefinitionPage, RunAgentRef,
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
    identity_bot_id: str | None = None
    """The Bot whose passport identity this run's Bot agents inherit.

    ``None`` keeps the ordinary flow, where each Bot agent applies for its own
    passport and may park at the human authorization gate.
    """
    agent_refs: tuple[AgentDefinitionRef, ...] = ()
    """The roster as stored-definition references.

    Supply either ``agents`` (inline, unchanged) or ``agent_refs``, never both.
    References are resolved to specs at submit, before any run row is written,
    and the resolved provenance is recorded beside the catalog snapshot.
    """


@dataclass(frozen=True)
class AgentDefinitionRef:
    """One reference to a stored agent definition.

    ``alias`` becomes the catalog name the orchestrator spawns under; without
    one the definition's own name is used.
    """

    definition_id: AgentDefinitionId
    alias: str | None = None


@dataclass(frozen=True)
class ResolvedRoster:
    """A submit's roster after reference resolution.

    ``specs`` is what the run executes; ``refs`` is the provenance to record
    beside the catalog snapshot (empty for an inline roster).
    """

    specs: tuple[AgentSpec, ...]
    refs: tuple[RunAgentRef, ...] = ()


@dataclass(frozen=True)
class BotAuthorizationDescriptor:
    """The pending human-authorization gate for one agent's bot.

    The shared-contract shape — the host adapter's status result and the
    run surfaces serialize the same five fields: ``state`` is the parked
    BotState value (``AWAITING_AUTHORIZATION``), ``iframe_url`` /
    ``redirect_url`` the passport application's consent surfaces as
    submitted (empty when the payload has none), ``deadline`` the
    authorization window's end as the ISO-8601 spelling the creation-job
    payload itself carries. ``application_id`` is the passport
    application's identifier as the facade's status answer names it
    (empty when the answer carries none) — a consumer can route a human
    to the decision surface from the run read alone, with no direct
    listing against the passport facade. The creation payload is the
    only durable copy of the URL/window facts — this descriptor is a
    read-side join, never a second stored one. Present exactly while
    the application is actually PENDING inside its window (the live
    status answer decides, never a cached provisioning-state write);
    auto-issued, approved, rejected, expired and terminal bots surface
    none.
    """
    state: str
    iframe_url: str
    redirect_url: str
    deadline: str
    application_id: str


@dataclass(frozen=True)
class IdentitySourceRefusal:
    """Why a named Bot cannot serve as a run's identity source.

    ``code`` is one of the three refusal reasons a caller can act on
    differently: :data:`IDENTITY_SOURCE_NOT_OWNED` (name a Bot of your own),
    :data:`IDENTITY_SOURCE_NO_PASSPORT` (issue a passport, or name another
    Bot), :data:`IDENTITY_SOURCE_UNVERIFIED` (the passport could not be
    resolved; retry). Every code carries a populated ``message``; a code
    answered with an empty one reads as the generic invalid-source
    refusal, so a seam in another tree can still raise ``code`` alone.
    """

    code: str
    message: str = ""


#: The Bot is absent from the caller's rows -- it does not exist, or it
#: belongs to someone else (the two are deliberately indistinguishable).
IDENTITY_SOURCE_NOT_OWNED: Final = "not_owned"
#: Neither the Bot's cached passport nor the provider establishes an issued
#: agent credential, so the Bot genuinely holds no usable identity.
IDENTITY_SOURCE_NO_PASSPORT: Final = "no_passport"
#: The passport could not be resolved (provider failure, no seam bound).
#: Never means "has no passport" -- that is ``no_passport``.
IDENTITY_SOURCE_UNVERIFIED: Final = "unverified"


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
    descriptor the host adapter reports, so there is exactly one
    source of truth and zero copied state.
    """

    @abstractmethod
    def identity_source(
        self, *, owner_id: str, bot_id: str,
    ) -> str | IdentitySourceRefusal:
        """The agent code of a Bot that may serve as an identity source.

        An :class:`IdentitySourceRefusal` when it may not, naming which of
        the three cases applied: the Bot is absent or not owned by
        ``owner_id``, it holds no issued passport, or its passport could not
        be resolved. The service uses this to refuse a submit before any
        agent row is written, without importing the bot repository itself
        (D17/D35).
        """

    @abstractmethod
    def authorization(self, *, owner_id: str, bot_id: str) -> BotAuthorizationDescriptor | None:
        """The gate descriptor for a (owner, bot) creation, or ``None`` when
        no durable creation payload exists for it. Must not raise on
        infrastructure errors — callers degrade, never fail the read."""
        ...


@runtime_checkable
class ManifestDocumentValidator(Protocol):
    """Write-time validation seam for a definition's manifest document.

    Definitions are human-authored configuration, so their manifest is checked
    **once, when written**, rather than on every run that uses it. The grammar
    is owned by the platform's manifest preflight, which the swarm core may not
    import (D17/D35), so this seam carries only the verdict across the boundary.

    Declared required rather than optional: a definition write is a promise that
    the document was accepted, so a profile that cannot supply this seam must
    refuse definition writes rather than store unvalidated manifests.
    """

    @abstractmethod
    def validate_document(self, document: str) -> None:
        """Raise :class:`DefinitionManifestInvalid` when the document is
        refused; return normally when it is acceptable. An empty document
        means "no manifest" and is always acceptable."""
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
    construction, so a mixed run snapshots each bot independently.
    """
    agent_id: str
    name: str
    status: str
    kind: str
    spawn_round: int
    bot_id: str | None
    bot_state: str
    authorization: BotAuthorizationDescriptor | None = None
    objective: str | None = None


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
    # task_spec.objective: the instruction this dispatch was given. A row is
    # created per dispatch, so a re-dispatch of one catalog entry carries its
    # own and can differ from the first.
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
    objective: str | None = None


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
    agent_refs: dict[str, RunAgentRef] = field(default_factory=dict)
    """Definition provenance, keyed by catalog name.

    One entry per catalog that came from a stored definition; empty for an
    inline roster. Relating to the entry (not the spawned instance) is what lets
    a run that never spawned still report where it came from.
    """


class AgentSwarmRunNotFoundError(Exception):
    """run_events on a run id the tenant cannot see (404-class)."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run not found: {run_id}")


class AgentSwarmConfigError(Exception):
    """Fail-clear submit refusal: the LLM config is missing/empty (400-class)."""


class IdentitySourceRequired(Exception):
    """A submit named no identity source (400-class).

    Every agentswarm Bot agent runs under an existing Bot's passport
    identity, so a submit without one cannot be served at all — there is no
    approval-gated path. Refused before any row is written.
    """

    def __init__(self) -> None:
        super().__init__(
            "agentswarm submit requires identity_bot_id: name the Bot whose "
            "passport identity this run's Bot agents inherit"
        )


class IdentitySourceInvalid(Exception):
    """A named identity source cannot supply identity (422-class).

    ``reason`` is one of :data:`IDENTITY_SOURCE_NOT_OWNED`,
    :data:`IDENTITY_SOURCE_NO_PASSPORT`, :data:`IDENTITY_SOURCE_UNVERIFIED`
    and ``message`` says what the caller should do about it -- the three
    cases need different actions, so the text differs per case. Distinct
    from :class:`IdentitySourceRequired`, which is about the field being
    absent rather than wrong.
    """

    def __init__(self, bot_id: str, reason: str, *, message: str = "") -> None:
        self.bot_id = bot_id
        self.reason = reason
        self.message = message
        # The generic text names the id; a per-case caller (the HTTP adapter)
        # supplies the actionable wording from ``reason`` when none came with
        # the refusal.
        super().__init__(message or f"identity source {bot_id!r} is not usable")


class AgentReferenceUnresolved(Exception):
    """A submit named a definition that cannot supply its roster (422-class).

    Covers all three indistinguishable cases deliberately: absent, deleted, and
    owned by someone else. They answer alike so the id space cannot be probed,
    and the whole submit is refused rather than a partial roster executed.
    """

    def __init__(self, definition_id: str) -> None:
        self.definition_id = definition_id
        super().__init__(
            f"agent definition {definition_id!r} is not available"
        )


class DefinitionManifestInvalid(Exception):
    """A definition's manifest document was refused by the platform preflight.

    Raised at write time, never at submit: an accepted definition is a promise
    that its document was valid when stored. ``message`` carries the platform's
    own diagnostic so the caller can fix the document.
    """

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message or "manifest document was rejected")


class DefinitionNameConflict(Exception):
    """The caller already owns a live definition with this name (409-class)."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"a live definition named {name!r} already exists")


class DefinitionRowConflict(Exception):
    """The version the caller was editing is no longer the live one (409-class).

    A definition version is a row; an edit carries the row id it read. When
    that row has been superseded by a concurrent edit the write is refused
    rather than stacked on top of it, and no version is created.
    """

    def __init__(self, row_id: int) -> None:
        self.row_id = row_id
        super().__init__(
            f"definition version {row_id} is no longer the live one"
        )


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
                  *, page: int = 1, page_size: int = 20,
                  owner_id: str | None = None,
                  sort: str | None = None, order: str | None = None,
                  keyword: str | None = None) -> RunPage: ...

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

    # ── agent definitions (per-user; every call carries the caller's owner id) ──

    @abstractmethod
    def create_definition(
        self, *, tenant: str, owner_id: str, name: str, role: str = "",
        instructions: str = "", kind: AgentKind = AgentKind.LLM,
        model: str | None = None, engine: str = "",
        bot_type: str = "personal", manifest_document: str = "",
    ) -> AgentDefinition: ...

    @abstractmethod
    def get_definition(self, definition_id: AgentDefinitionId, tenant: str,
                       owner_id: str) -> AgentDefinition | None: ...

    @abstractmethod
    def list_definitions(self, tenant: str, owner_id: str,
                         name_filter: str | None = None,
                         page: int = 1, page_size: int = 20
                         ) -> AgentDefinitionPage: ...

    @abstractmethod
    def list_definition_versions(self, definition_id: AgentDefinitionId,
                                 tenant: str,
                                 owner_id: str) -> list[AgentDefinition]: ...

    @abstractmethod
    def update_definition(
        self, *, definition_id: AgentDefinitionId, tenant: str, owner_id: str,
        expected_row_id: int, name: str, role: str = "",
        instructions: str = "", kind: AgentKind = AgentKind.LLM,
        model: str | None = None, engine: str = "",
        bot_type: str = "personal", manifest_document: str = "",
    ) -> AgentDefinition: ...

    @abstractmethod
    def delete_definition(self, definition_id: AgentDefinitionId, tenant: str,
                          owner_id: str) -> int: ...
