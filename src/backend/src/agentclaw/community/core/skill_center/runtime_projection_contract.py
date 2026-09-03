"""Core contract for applying one Bot capability projection."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Optional, Protocol, runtime_checkable

from agentclaw.community.core.skill_center.runtime_resolver import (
    RegisteredSkillAsset,
    RuntimeProjection,
    RuntimeSkillProjection,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping


class RuntimeProjectionStatus(StrEnum):
    """User-visible outcome of applying a committed Desired State."""

    CONVERGED = "CONVERGED"
    PENDING = "PENDING"
    DEGRADED = "DEGRADED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class RuntimeProjectionIssue:
    """Sanitized, actionable evidence for one runtime resource or domain."""

    resource_type: str
    code: str
    reason: str
    status: RuntimeProjectionStatus
    retryable: bool
    resource_id: str | None = None
    name: str | None = None
    corpus: str | None = None
    requested_action: str | None = None
    observed_entry_type: str | None = None
    expected_entry_type: str | None = None
    logical_location: str | None = None
    suggested_action: str | None = None

    def to_dict(self) -> dict[str, object]:
        values: dict[str, object] = {
            "resource_type": self.resource_type,
            "code": self.code,
            "reason": self.reason,
            "status": self.status.value,
            "retryable": self.retryable,
        }
        for key in (
            "resource_id",
            "name",
            "corpus",
            "requested_action",
            "observed_entry_type",
            "expected_entry_type",
            "logical_location",
            "suggested_action",
        ):
            value = getattr(self, key)
            if value is not None:
                values[key] = value
        return values


@dataclass(frozen=True, slots=True)
class RuntimeProjectionResult:
    """A projection outcome separate from Installation Desired State."""

    status: RuntimeProjectionStatus
    components: dict[str, RuntimeProjectionStatus] = field(default_factory=dict)
    issues: tuple[RuntimeProjectionIssue, ...] = ()
    reason: str | None = None

    @classmethod
    def converged(cls, *, components: dict[str, RuntimeProjectionStatus] | None = None) -> "RuntimeProjectionResult":
        return cls(
            status=RuntimeProjectionStatus.CONVERGED,
            components=components or {},
        )

    @classmethod
    def skipped(cls, *, reason: str) -> "RuntimeProjectionResult":
        return cls(status=RuntimeProjectionStatus.SKIPPED, reason=reason)

    @classmethod
    def pending(
        cls,
        *,
        code: str,
        reason: str,
        suggested_action: str | None = None,
    ) -> "RuntimeProjectionResult":
        return cls(
            status=RuntimeProjectionStatus.PENDING,
            issues=(
                RuntimeProjectionIssue(
                    resource_type="RUNTIME",
                    code=code,
                    reason=reason,
                    status=RuntimeProjectionStatus.PENDING,
                    retryable=True,
                    suggested_action=suggested_action,
                ),
            ),
        )

    @classmethod
    def combine(cls, *results: "RuntimeProjectionResult") -> "RuntimeProjectionResult":
        if not results:
            return cls.skipped(reason="NO_RUNTIME_COMPONENT")
        components: dict[str, RuntimeProjectionStatus] = {}
        issues: list[RuntimeProjectionIssue] = []
        statuses: list[RuntimeProjectionStatus] = []
        for result in results:
            components.update(result.components)
            issues.extend(result.issues)
            statuses.append(result.status)
        if RuntimeProjectionStatus.DEGRADED in statuses:
            status = RuntimeProjectionStatus.DEGRADED
        elif RuntimeProjectionStatus.PENDING in statuses:
            status = RuntimeProjectionStatus.PENDING
        elif RuntimeProjectionStatus.CONVERGED in statuses:
            status = RuntimeProjectionStatus.CONVERGED
        else:
            status = RuntimeProjectionStatus.SKIPPED
        return cls(status=status, components=components, issues=tuple(issues))

    def to_dict(self) -> dict[str, object]:
        pending_count = sum(
            issue.status is RuntimeProjectionStatus.PENDING
            for issue in self.issues
        )
        degraded_count = sum(
            issue.status is RuntimeProjectionStatus.DEGRADED
            for issue in self.issues
        )
        data: dict[str, object] = {
            "status": self.status.value,
            "components": {
                name: status.value for name, status in self.components.items()
            },
            "pending_count": pending_count,
            "degraded_count": degraded_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }
        if self.reason is not None:
            data["reason"] = self.reason
        return data


@runtime_checkable
class CapabilityRuntimeBoundary(Protocol):
    """The runtime writes a capability projection is allowed to make.

    Declared here rather than imported so the contract names a boundary
    instead of an implementation: an ``EngineRuntimeProjection`` may write
    the Skill half, the MCP half, or both, and nothing else. ``SkillSetService``
    satisfies this structurally; the composition root is what pairs the two,
    and substituting a different runtime service is a change there, not here.

    Both signatures mirror that service exactly, deliberately — this narrows
    the visible surface, it does not restate or re-specify it. What each call
    guarantees is documented on ``SkillSetService``.
    """

    async def project_skills(
        self,
        *,
        desired_skills: Optional[list[dict]] = None,
        effective_mcps: Optional[list[dict]] = None,
    ) -> bool:
        """Apply one complete resolver-owned Skill snapshot to the runtime.

        Blocking work sits behind this — device resolution, and on a
        whole-artifact engine an artifact compose and the outbound apply
        request — but keeping off the event loop is the implementation's
        responsibility, not the caller's. Await it like ``project_mcps``.

        ``effective_mcps`` is for the whole-artifact case only, and for the
        same reason ``desired_skills`` is passed: the compose behind this call
        re-reads desired state that plan resolution already read, and handing
        the resolved value over is what avoids the second pass. A runtime with
        a separate MCP endpoint composes nothing here and leaves it ``None``.
        """
        ...

    async def project_mcps(
        self,
        *,
        claimed: frozenset[str],
        released: frozenset[str],
        declared: set[str],
    ) -> bool:
        """Apply one MCP projection: deliver, withdraw, and declare the set."""
        ...

    async def project_whole_artifact(
        self,
        *,
        desired_skills: list[dict],
        effective_mcps: list[dict] | None = None,
    ) -> bool:
        """Compose and apply one structure-only whole-artifact snapshot.

        No filesystem mapping is built before this call. Exact Center identity
        reaches the artifact composer as data, so a whole-artifact engine never
        guesses a path or trips over the legacy symlink adapter.
        """
        ...


@dataclass(frozen=True, slots=True)
class ResolvedSkillPlan:
    """One Bot's complete Skill state, resolved and ready to apply.

    The Installation flush, Skill validation, and mapping resolution have all
    completed before this value crosses the engine seam. It deliberately says
    nothing about MCP, CLI, or Passport state.

    It exists as a value rather than a tuple because it crosses a seam — an
    ``EngineRuntimeProjection`` acts from the plan alone — and a positional
    six-tuple unpacked at three call sites was one reordering away from a
    silent mix-up.
    """

    #: The Bot this plan is for, and the owner whose desired state it reads.
    bot_id: str
    owner_id: str
    #: The runtime boundary an implementation writes through. Narrowed to the
    #: two calls a projection may make; see ``CapabilityRuntimeBoundary``.
    service: CapabilityRuntimeBoundary
    #: The ``ac_bots`` row. Carries ``env`` / ``entity_id`` / ``active_engine``
    #: that a filesystem-layout decision needs.
    bot: dict
    #: ``ac_bots.active_engine`` — the registry key. Whose runtime contract
    #: applies is decided from this and nothing else.
    engine: str
    #: The complete Skill half. It is a distinct type from RuntimeProjection,
    #: so an engine cannot mistake omitted Non-Skill state for an empty final
    #: MCP/CLI snapshot.
    projection: RuntimeSkillProjection


@dataclass(frozen=True, slots=True)
class ResolvedCapabilityPlan(ResolvedSkillPlan):
    """One Bot's complete Skill/MCP/CLI state, resolved before any write."""

    #: The complete engine-neutral projection. This field deliberately narrows
    #: the base type: a ResolvedCapabilityPlan can always satisfy a Skill-only
    #: consumer, while its MCP consumers receive a genuine full snapshot.
    projection: RuntimeProjection
    #: Effective Default CLI facts, as the authorization service holds them,
    #: ready for the overwrite-style Passport update.
    effective_cli_items: list[dict]
    #: The Bot's effective MCP set — default policy ∪ installed ∪ Skill
    #: dependencies — as ``collect_bot_active_mcps`` resolved it for the
    #: projected MCP codes. Carried rather than recomputed because a
    #: whole-artifact engine composes its document from the same set: without
    #: this the delivery would re-read it from the database it was just read
    #: from. Bare association entries, no MCP Center detail merged in.
    effective_mcp_entries: list[dict]
    #: Per-MCP execution identity, resolved during plan resolution because it
    #: can fail — see ``BotRuntimeProjector._resolve_mcp_identity_modes``.
    identity_modes: Mapping[str, object]


@runtime_checkable
class EngineRuntimeProjection(Protocol):
    """How one engine's runtime consumes a capability projection.

    The projector resolves one of these per Bot and delegates; it does not
    itself know what any engine is. Two things vary between engines and both
    live here: what desired state a runtime can carry at all, and how many
    runtime calls converging on it takes.

    That second question is genuinely the engine's. A runtime with separate
    Skill and MCP endpoints saves a round trip by writing only the half a
    mutation touched. A whole-artifact runtime recomposes its entire
    configuration from the database on every call and discards the arguments,
    so a second call restates in full what the first already delivered. The
    same ``ProjectionScope`` means different things to the two, and neither
    reading is more correct — which is why the caller must not pick one.

    Both methods are abstract. The implementations name this protocol as a
    base, so a subclass that omits one fails at construction — in the
    composition root, at startup — instead of raising ``AttributeError`` at
    the moment a projection tries to use it.
    """

    @abstractmethod
    def validate_plan(
        self,
        *,
        skill_assets: Sequence[RegisteredSkillAsset],
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None:
        """Refuse desired state this runtime has no contract for.

        Called during plan resolution — before any runtime, MCP, Passport or
        probe request is emitted — so a refusal costs nothing to unwind.
        Raises ``SkillSetRuntimeReconcileError``; returns ``None`` when the
        plan is carryable.
        """
        ...

    @abstractmethod
    async def apply(
        self,
        *,
        plan: ResolvedSkillPlan,
        scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> RuntimeProjectionResult:
        """Converge this Bot's runtime on ``plan``.

        Returns an explicit observed Runtime outcome.  Desired State is
        already committed by the caller, so transport/filesystem drift is not
        represented by a control-plane exception. How many runtime calls that
        took, and in what order, is decided here and is not observable to the
        caller. MCP writers require ``ResolvedCapabilityPlan``; Skill-only
        writers accept the narrower ``ResolvedSkillPlan``.
        """
        ...


@dataclass(frozen=True)
class ProjectionScope:
    """What one mutation changed, as the mutation itself knows it.

    Declared, never inferred. ``add_mcp`` holds the code it claimed and
    ``remove_mcp`` the one it released, so re-deriving them downstream — by
    diffing a before/after snapshot, say — would be a second source of truth
    for a fact the caller already has, and a second copy of the set-union
    logic that could drift from the projection's own.

    The projector treats ``claimed_mcp`` / ``released_mcp`` as a *guard*
    input, never a source: it intersects them with the projected set, so a
    scope can only ever shrink there. That keeps a single-MCP mutation a
    single device write, and stops a release from deleting a code the
    default policy or a Skill dependency still supplies.

    What this scope *causes* is the reading engine's decision, not a promise
    made here. The guarantees described on these fields — "a single-MCP add
    stays a single device write", ``claim_all_mcp``'s empty-container
    premise, the deliver-before-declare ordering — are
    ``PerDomainRuntimeProjection``'s, and hold where the halves have separate
    runtime endpoints. A whole-artifact engine carries both halves in one
    document composed from the database, so for it the scope selects nothing
    about content and only how many identical copies would be sent; its
    implementation reads the scope accordingly. See
    ``EngineRuntimeProjection``. A mutation still declares what it changed
    the same way for every engine — only the reading differs.
    """

    #: Project the Skill half — publish mappings, or sync the legacy runtime.
    skills: bool = False
    #: Project the MCP half — device delivery, the allow-list, and Passport.
    mcp: bool = False
    #: MCP codes this mutation added to the Bot. Configuration is pushed for
    #: these and no others, so a one-MCP add stays a one-MCP device write.
    claimed_mcp: frozenset[str] = frozenset()
    #: MCP codes this mutation took away. Configuration is deleted for these
    #: and no others.
    released_mcp: frozenset[str] = frozenset()
    #: Treat *every* projected MCP code as claimed, ignoring ``claimed_mcp``.
    #:
    #: Exactly one caller sets this: the device-activated listener
    #: (``SkillSymlinkListener``, via ``DeviceActivatedEvent``). It is the one
    #: place where "just re-declare the allow-list" is not enough, because a
    #: newly active container holds *no* MCP configuration to refresh — there
    #: is no prior state to converge on, only an empty device. Every other
    #: path is mutation-triggered and names its own delta.
    #:
    #: It is load-bearing, not a convenience: ``report_device_alive``
    #: deliberately does not push MCP details itself (see
    #: ``test_pending_activation_has_one_mcp_writer_owned_by_device_event``,
    #: which asserts the legacy ``_sync_mcps_when_device_active`` stays
    #: uncalled), so this projection is the only production path that writes
    #: per-MCP configuration to a freshly activated device. Drop it and every
    #: MCP is whitelisted with no endpoint or api_key behind it.
    #:
    #: A flag rather than a code set because the caller cannot fill
    #: ``claimed_mcp``: the projected set is only known once the projector has
    #: resolved the plan, which is after the caller is done.
    #:
    #: It says nothing about which halves run: ``skills`` and ``mcp`` decide
    #: that on their own. An MCP-only reconcile — ``ProjectionScope(mcp=True,
    #: claim_all_mcp=True)`` — is a real and used shape.
    #:
    #: It never deletes: ``released_mcp`` is not consulted when this is set,
    #: so a reconcile can only ever add configuration to a device.
    claim_all_mcp: bool = False

    @classmethod
    def everything(cls) -> "ProjectionScope":
        """Both halves, with every projected MCP code counted as claimed.

        The shape for the device-activated listener, which has no mutation to
        describe and faces a container that may hold nothing. See
        ``claim_all_mcp`` for why that case cannot be served by a refresh.
        """
        return cls(skills=True, mcp=True, claim_all_mcp=True)

    def inverted(self) -> "ProjectionScope":
        """The same scope as a compensating projection would apply it.

        What the forward projection claimed is what an undo releases, and
        vice versa — mirroring how ``retired_logical_skill_mappings`` is
        called with its arguments swapped on the compensating path.
        """
        return replace(
            self,
            claimed_mcp=self.released_mcp,
            released_mcp=self.claimed_mcp,
        )

@runtime_checkable
class BotRuntimeProjectorProtocol(Protocol):
    """Apply database desired state through the selected runtime authority."""

    async def snapshot_skill_mappings(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> tuple[PoolSkillMapping, ...]:
        """Return the current desired Skill mappings without publishing them."""
        ...

    async def project(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult:
        """Apply the projection, limited to what ``scope`` says changed.

        ``scope`` is required rather than defaulted: a caller that forgot it
        would silently get a full reconcile, which is the expensive answer and
        never the one a mutation wants. Callers with genuinely nothing to
        declare say so with ``ProjectionScope.everything()``.
        """
        ...

    async def project_mcp_and_cli(
        self,
        *,
        bot_id: str,
        owner_id: str,
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult:
        """Project MCP/CLI while an external authority owns Skill mappings.

        ``scope.mcp`` must be true; callers cannot use this entry point to
        smuggle a Skill-only projection around the normal ``project`` seam.
        """
        ...


__all__ = [
    "BotRuntimeProjectorProtocol",
    "CapabilityRuntimeBoundary",
    "EngineRuntimeProjection",
    "ProjectionScope",
    "RuntimeProjectionIssue",
    "RuntimeProjectionResult",
    "RuntimeProjectionStatus",
    "ResolvedCapabilityPlan",
    "ResolvedSkillPlan",
]
