"""Transport-free L6 product composition contracts (not Task/Method state)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)

from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenPublicationProfile,
)


@dataclass(frozen=True)
class VersionedProfileRef:
    profile_id: str
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ContextCollabValidationError("profile_id must be a non-empty string")
        if type(self.version) is not int or self.version < 1:
            raise ContextCollabValidationError(
                "profile version must be a positive integer"
            )


@dataclass(frozen=True)
class MethodRequirement:
    method: str
    version: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(item, str) or not item.strip()
            for item in (self.method, self.version)
        ):
            raise ContextCollabValidationError(
                "Method requirement needs non-empty method and version"
            )


@dataclass(frozen=True)
class MethodCompositionTemplate:
    """Current implementation supports one explicit top-level binding, no inferred children."""

    method_kind: str
    scope: str
    required_methods: tuple[MethodRequirement, ...]
    optional_methods: tuple[MethodRequirement, ...] = ()


@dataclass(frozen=True)
class ScenarioRole:
    name: str
    l4_role: str
    participant_source: str  # principal_ref / executor_ref
    required: bool
    responsibilities: tuple[str, ...]


@dataclass(frozen=True)
class DomainMapping:
    term: str
    authority: str  # An L2-L5 object, never a second product-owned authority.


@dataclass(frozen=True)
class DomainProfile:
    ref: VersionedProfileRef
    mappings: tuple[DomainMapping, ...]


@dataclass(frozen=True)
class WorkflowAction:
    """A product action maps to an L4 event, not another transition table."""

    name: str
    event_kind: str
    primitive_type: str
    payload_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.name, self.event_kind, self.primitive_type)
        ):
            raise ContextCollabValidationError(
                "workflow action requires non-empty names"
            )
        if len(set(self.payload_fields)) != len(self.payload_fields):
            raise ContextCollabValidationError(
                "duplicate workflow action payload field"
            )


@dataclass(frozen=True)
class WorkflowProfile:
    ref: VersionedProfileRef
    # None is intentional: discussion is an event stream, with no task machine.
    state_machine_ref: VersionedProfileRef | None
    completion_states: tuple[str, ...]
    compensation_events: tuple[str, ...]
    strict_control_events: bool = False
    executor_submission_only: bool = False
    message_parent_only: bool = False
    public_events_only: bool = False
    actions: tuple[WorkflowAction, ...] = ()

    def __post_init__(self) -> None:
        names = [action.name for action in self.actions]
        events = [action.event_kind for action in self.actions]
        if len(set(names)) != len(names) or len(set(events)) != len(events):
            raise ContextCollabValidationError("duplicate workflow action mapping")

    def action(self, name: str) -> WorkflowAction:
        for action in self.actions:
            if action.name == name:
                return action
        raise ContextCollabValidationError(f"workflow has no declared action {name!r}")


@dataclass(frozen=True)
class GovernanceRequirements:
    """Implementation contract IDs, not claims of remote governance conformance."""

    policy: str
    reliability: str
    observation: str
    consistency: str


@dataclass(frozen=True)
class CompatibilityRequirements:
    accs_revision: str
    required_layers: tuple[str, ...]
    required_extensions: tuple[str, ...]
    optional_extensions: tuple[str, ...]
    downgrade_policy: str
    migration_policy: str
    cross_agent_delivery: str = "L1/A2A"


@dataclass(frozen=True)
class ProductProfileDefinition:
    ref: VersionedProfileRef
    composition: MethodCompositionTemplate
    scenario_roles: tuple[ScenarioRole, ...]
    domain: DomainProfile
    workflow: WorkflowProfile
    governance: GovernanceRequirements
    compatibility: CompatibilityRequirements
    # None means this product declares no automatic Open publication use case.
    publication: OpenPublicationProfile | None = None

    def require_publication(self) -> OpenPublicationProfile:
        if self.publication is None:
            raise ContextCollabValidationError(
                "product has no Open publication contract"
            )
        return self.publication


@dataclass(frozen=True)
class ProductCapabilities:
    """Explicit capability offer, usable for local selection or a future L1 peer exchange."""

    profiles: tuple[VersionedProfileRef, ...]
    methods: tuple[MethodRequirement, ...]
    accs_revisions: tuple[str, ...]
    layers: tuple[str, ...]
    extensions: tuple[str, ...]
    governance: tuple[str, ...]


@dataclass(frozen=True)
class NegotiationResult:
    selected_profile: VersionedProfileRef
    accepted: bool
    missing_capabilities: tuple[str, ...]
    omitted_optional_capabilities: tuple[str, ...]
    reason: str
    mode: str = "local_exact_match"


def negotiate(
    profile: ProductProfileDefinition, offer: ProductCapabilities
) -> NegotiationResult:
    """No silent fallback: missing required capabilities produce an explicit rejection."""
    missing: list[str] = []
    optional: list[str] = []
    if profile.ref not in offer.profiles:
        missing.append(f"product:{profile.ref.profile_id}@{profile.ref.version}")
    if profile.compatibility.accs_revision not in offer.accs_revisions:
        missing.append(f"accs:{profile.compatibility.accs_revision}")
    for layer in profile.compatibility.required_layers:
        if layer not in offer.layers:
            missing.append(f"layer:{layer}")
    for method in profile.composition.required_methods:
        if method not in offer.methods:
            missing.append(f"method:{method.method}@{method.version}")
    for method in profile.composition.optional_methods:
        if method not in offer.methods:
            optional.append(f"method:{method.method}@{method.version}")
    for extension in profile.compatibility.required_extensions:
        if extension not in offer.extensions:
            missing.append(f"extension:{extension}")
    for extension in profile.compatibility.optional_extensions:
        if extension not in offer.extensions:
            optional.append(f"extension:{extension}")
    for requirement in asdict(profile.governance).values():
        if requirement not in offer.governance:
            missing.append(f"governance:{requirement}")
    return NegotiationResult(
        selected_profile=profile.ref,
        accepted=not missing,
        missing_capabilities=tuple(missing),
        omitted_optional_capabilities=tuple(optional),
        reason="exact required contract match"
        if not missing
        else "required capabilities unavailable; downgrade forbidden",
    )
