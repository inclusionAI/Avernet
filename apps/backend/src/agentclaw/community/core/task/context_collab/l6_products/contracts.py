"""Transport-free L6 product composition contracts (not Task/Method state)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    MethodProfile,
    MethodProfileRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    MethodPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
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
class MethodRelationTemplate:
    """A directed L5 relation between required Method Profiles.

    Endpoints do not imply hierarchy; ``relation`` defines their semantics.
    For NEST only, the source contains the target Method.
    """

    source_method: str
    target_method: str
    relation: CompositionRelation


@dataclass(frozen=True)
class MethodTemplate:
    """An exact L5 Profile and its own optional Method-specific product policy.

    No policy means structural participation only, not an implicit Open
    publication or autonomous Swarm capability. This is an authoring envelope,
    not a replacement for the L5 MethodProfile contract.
    """

    profile: MethodProfile
    policy: MethodPolicy | None = None


@dataclass(frozen=True)
class MethodCompositionTemplate:
    """Compose L5 MethodProfiles, not a second L6 Method requirement model.

    Instantiation always targets an existing Collet; scope is not configurable.
    Relations instantiate one binding per required Profile and explicit L5 edges.
    Empty relations retain the historical single-binding topology.
    Required MethodProfiles are the sole source of Method kinds.
    """

    required_methods: tuple[MethodTemplate, ...]
    optional_methods: tuple[MethodTemplate, ...] = ()
    relations: tuple[MethodRelationTemplate, ...] = ()

    def require_policy(self, method: str) -> MethodPolicy:
        """Return the exact declared policy; never infer one from Method kind."""
        policies = [
            item.policy
            for item in self.required_methods
            if item.profile.method_kind == method
            and item.policy is not None
            and item.policy.method == method
        ]
        if len(policies) != 1:
            raise ContextCollabValidationError(
                f"composition requires exactly one {method} Method policy"
            )
        return policies[0]


def composition_method_kind(composition: MethodCompositionTemplate) -> str:
    """Project required Profiles to the existing creation/legacy binding wire.

    This string is derived only at compatibility boundaries, not authored or
    stored as another field on the composition template. Optional Methods are
    not selected implicitly.
    """
    return " + ".join(
        method.profile.method_kind for method in composition.required_methods
    )


@dataclass(frozen=True)
class ScenarioRole:
    name: str
    l4_role: str
    participant_source: str  # principal_ref / executor_ref / agent_ref / open_admission
    required: bool
    responsibilities: tuple[str, ...]
    # A fixed, profile-declared Agent assignment; never a resource grant.
    # None is valid for the two identity sources supplied at instantiation.
    agent_ref: AgentRef | None = None


@dataclass(frozen=True)
class DomainMapping:
    term: str
    authority: str  # An L2-L5 object, never a second product-owned authority.


@dataclass(frozen=True)
class DomainProfile:
    ref: VersionedProfileRef
    mappings: tuple[DomainMapping, ...]


@dataclass(frozen=True)
class TaskAction:
    """A Work action maps to an L4 event, not a Bot execution plan."""

    name: str
    event_kind: str
    primitive_type: str
    payload_fields: tuple[str, ...]
    # None adds no action-specific role restriction; G1 and G4 still apply.
    required_role: str | None = None

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


# Compatibility import for existing authored WorkflowProfiles.
WorkflowAction = TaskAction


@dataclass(frozen=True)
class TaskLifecycleContract:
    """Versioned Work semantics over L4 Task, independent of product orchestration.

    Transitions/guards remain solely in the referenced G4 machine. The registry
    owns exact immutable versions; products select them, never copy the machine.
    """

    ref: VersionedProfileRef
    state_machine_ref: VersionedProfileRef
    completion_states: tuple[str, ...]
    compensation_events: tuple[str, ...]
    actions: tuple[TaskAction, ...]
    strict_control_events: bool = True
    executor_submission_only: bool = True
    public_events_only: bool = True

    def __post_init__(self) -> None:
        names = [action.name for action in self.actions]
        events = [action.event_kind for action in self.actions]
        if len(set(names)) != len(names) or len(set(events)) != len(events):
            raise ContextCollabValidationError("duplicate Task action mapping")

    def action(self, name: str) -> TaskAction:
        for action in self.actions:
            if action.name == name:
                return action
        raise ContextCollabValidationError(
            f"Task contract has no declared action {name!r}"
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
class LegacyProductConfiguration:
    ref: VersionedProfileRef
    composition: MethodCompositionTemplate
    scenario_roles: tuple[ScenarioRole, ...]
    governance: GovernanceRequirements
    compatibility: CompatibilityRequirements
    # None means standard L3/L4 vocabulary, not a fabricated domain profile.
    domain: DomainProfile | None = None
    # None means no product orchestration, NOT an unrestricted Task lifecycle.
    workflow: WorkflowProfile | None = None
    # With no workflow, omission selects the fixed standard Task contract @1.
    # With a legacy workflow, None preserves its existing Task/action contract.
    task_contract_ref: VersionedProfileRef | None = None
    participant_scope: ParticipantScope = ParticipantScope()


@dataclass(frozen=True)
class ProductCapabilities:
    """Explicit capability offer, usable for local selection or a future L1 peer exchange."""

    profiles: tuple[VersionedProfileRef, ...]
    methods: tuple[MethodProfileRef, ...]
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
    profile: LegacyProductConfiguration, offer: ProductCapabilities
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
    for declaration in profile.composition.required_methods:
        method = declaration.profile
        if method.ref not in offer.methods:
            missing.append(f"method:{method.method_kind}@{method.profile_version}")
    for declaration in profile.composition.optional_methods:
        method = declaration.profile
        if method.ref not in offer.methods:
            optional.append(f"method:{method.method_kind}@{method.profile_version}")
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
