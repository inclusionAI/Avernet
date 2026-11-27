"""Shared structural validation for authored and installed L6 product definitions."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    ROLE_SELECTED_AGENT,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    parse_method_kinds,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation,
    detect_cycle,
    validate_composition,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    MethodProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    CompatibilityRequirements,
    GovernanceRequirements,
    MethodRelationTemplate,
    MethodTemplate,
    LegacyProductConfiguration,
    TaskLifecycleContract,
    composition_method_kind,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    OpenMethodPolicy,
    SwarmMethodPolicy,
    SwarmWorkflowPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    resolve_event_contract,
)


def validate_product_profile(definition: LegacyProductConfiguration) -> None:
    """Reject unsupported composition/role/method-policy contracts without installing."""
    if not isinstance(definition.governance, GovernanceRequirements) or not isinstance(
        definition.compatibility, CompatibilityRequirements
    ):
        raise ContextCollabValidationError(
            "governance and compatibility require typed requirements"
        )
    if not isinstance(definition.participant_scope, ParticipantScope):
        raise ContextCollabValidationError(
            "participant_scope must be a typed ParticipantScope"
        )
    methods = definition.composition.required_methods
    for profiles in (methods, definition.composition.optional_methods):
        if not isinstance(profiles, tuple) or any(
            not isinstance(item, MethodTemplate)
            or not isinstance(item.profile, MethodProfile)
            for item in profiles
        ):
            raise ContextCollabValidationError(
                "composition Methods must be an immutable tuple of MethodTemplate declarations with L5 MethodProfile contracts"
            )
    # Preserve supported legacy creation/binding wires without a duplicate
    # author-supplied Method kind. Empty/duplicate/unsupported sets fail closed.
    parse_method_kinds(composition_method_kind(definition.composition))
    _validate_relations(definition)
    if definition.compatibility.downgrade_policy != "reject_missing_required":
        raise ContextCollabValidationError("unsupported downgrade policy")
    if definition.compatibility.migration_policy != "new_instances_only":
        raise ContextCollabValidationError("unsupported migration policy")
    names = [item.name for item in definition.scenario_roles]
    roles = [item.l4_role for item in definition.scenario_roles]
    if len(set(names)) != len(names) or len(set(roles)) != len(roles):
        raise ContextCollabValidationError("duplicate scenario role mapping")
    for role in definition.scenario_roles:
        if role.participant_source not in {
            "principal_ref",
            "executor_ref",
            "agent_ref",
            "open_admission",
        }:
            raise ContextCollabValidationError(
                "unsupported scenario role participant source"
            )
        if role.participant_source == "agent_ref":
            if not isinstance(role.agent_ref, AgentRef):
                raise ContextCollabValidationError(
                    "agent_ref role requires a typed AgentRef"
                )
            definition.participant_scope.require(role.agent_ref)
        elif role.agent_ref is not None:
            raise ContextCollabValidationError(
                "agent_ref requires agent_ref participant source"
            )
    deferred = [
        r for r in definition.scenario_roles if r.participant_source == "open_admission"
    ]
    if deferred and not any(
        d.profile.method_kind == "Open"
        and isinstance(d.policy, OpenMethodPolicy)
        and d.policy.publication.admission.participation_type
        is ParticipationType.COLLET_PARTICIPANT
        for d in methods
    ):
        raise ContextCollabValidationError(
            "open_admission roles require a required Open policy"
        )
    contract = resolve_event_contract(definition)
    if isinstance(contract, TaskLifecycleContract):
        if not any(
            role.l4_role == ROLE_SELECTED_AGENT
            and role.participant_source in {"executor_ref", "open_admission"}
            and role.required
            for role in definition.scenario_roles
        ):
            raise ContextCollabValidationError(
                "standard Task contract requires a designated executor role"
            )
    for action in contract.actions:
        if action.required_role is not None and action.required_role not in roles:
            raise ContextCollabValidationError(
                "workflow action requires an undeclared scenario role"
            )
    # Validate optional declarations too, but never enable their policies unless
    # they become required/selected in a separately authored composition.
    for declaration in (*methods, *definition.composition.optional_methods):
        policy = declaration.policy
        if policy is None:
            continue
        if not isinstance(
            policy, (OpenMethodPolicy, SwarmMethodPolicy, SwarmWorkflowPolicy)
        ):
            raise ContextCollabValidationError("unsupported Method policy type")
        if policy.method != declaration.profile.method_kind:
            raise ContextCollabValidationError(
                "Method policy must match its declared Method Profile"
            )
        if isinstance(policy, SwarmWorkflowPolicy):
            for node in policy.workflow.nodes:
                for agent in node.agents:
                    definition.participant_scope.require(agent)
        if isinstance(policy, OpenMethodPolicy):
            policy.publication.admission.validate_roles(definition.scenario_roles)
            if not contract.actions:
                raise ContextCollabValidationError(
                    "publication requires Task/workflow actions"
                )


def _validate_relations(definition: LegacyProductConfiguration) -> None:
    composition = definition.composition
    relations = composition.relations
    if not isinstance(relations, tuple) or any(
        not isinstance(item, MethodRelationTemplate) for item in relations
    ):
        raise ContextCollabValidationError(
            "Method relations must be an immutable tuple"
        )
    methods = {item.profile.method_kind for item in composition.required_methods}
    if methods == {"Open", "Single"} and not relations:
        raise ContextCollabValidationError(
            "Open + Single requires explicit Method relations"
        )
    pairs: list[tuple[str, str]] = []
    for item in relations:
        if item.parent_method not in methods or item.child_method not in methods:
            raise ContextCollabValidationError(
                "Method relation endpoint is not required"
            )
        if not isinstance(item.relation, CompositionRelation):
            raise ContextCollabValidationError(
                "Method relation requires CompositionRelation"
            )
        validate_composition(item.parent_method, item.relation, item.child_method)
        pairs.append((item.parent_method, item.child_method))
    if len(set(pairs)) != len(pairs) or detect_cycle(pairs):
        raise ContextCollabValidationError("duplicate or cyclic Method relations")
    if relations and {name for pair in pairs for name in pair} != methods:
        raise ContextCollabValidationError(
            "Method relations must cover all required Methods"
        )
