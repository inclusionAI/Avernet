"""L6 product selection and role-reference derivation.

L6 consumes the individual creation fields and the installed catalog.  It does
not construct an aggregate carrying Collet, Task, Method and Product state.
"""

from __future__ import annotations

from urllib.parse import quote

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    current_participation,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
)
from agentclaw.community.core.task.context_collab.l6_products.profile_reader import (
    ProductProfileReader,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    LegacyProductConfiguration,
    ScenarioRole,
    VersionedProfileRef,
    composition_method_kind,
    negotiate,
)


def product_definition(
    *,
    product_kind: str | None,
    workflow_kind: str | None,
    method_kind: str | None,
    profile_ref: VersionedProfileRef | None,
    catalog: ProductProfileReader,
) -> LegacyProductConfiguration | None:
    """Resolve the pinned L6 profile, or ``None`` for generic L4 creation."""
    if product_kind is None:
        if profile_ref is not None or workflow_kind is not None:
            raise ContextCollabValidationError(
                "a product profile/workflow requires product_kind"
            )
        return None
    version = profile_ref.version if profile_ref is not None else 1
    if profile_ref is not None and profile_ref.profile_id != product_kind:
        raise ContextCollabValidationError(
            "selected profile does not match product_kind"
        )
    profile = catalog.get(product_kind, version)
    expected_workflow = profile.workflow.ref.profile_id if profile.workflow else None
    if workflow_kind != expected_workflow:
        raise ContextCollabValidationError(
            f"workflow_kind {workflow_kind!r} is incompatible with product {product_kind!r}"
        )
    if method_kind != composition_method_kind(profile.composition):
        raise ContextCollabValidationError(
            f"method_kind {method_kind!r} is incompatible with product {product_kind!r}"
        )
    return profile


def validate_product_selection(
    *,
    product_kind: str | None,
    workflow_kind: str | None,
    method_kind: str | None,
    profile_ref: VersionedProfileRef | None,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    catalog: ProductProfileReader,
) -> LegacyProductConfiguration | None:
    """Validate profile topology and required ScenarioRole sources."""
    profile = product_definition(
        product_kind=product_kind,
        workflow_kind=workflow_kind,
        method_kind=method_kind,
        profile_ref=profile_ref,
        catalog=catalog,
    )
    if profile is None:
        return None
    validate_role_sources(profile, principal_ref, executor_ref)
    result = negotiate(profile, catalog.capabilities())
    if not result.accepted:
        raise ContextCollabValidationError(
            f"product negotiation rejected: {result.missing_capabilities}"
        )
    return profile


def validate_role_sources(
    profile: LegacyProductConfiguration,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
) -> None:
    """Validate typed subjects without renegotiating a historical selection."""
    if executor_ref is not None:
        profile.participant_scope.require(executor_ref)
    sources = {"principal_ref": principal_ref, "executor_ref": executor_ref}
    for role in profile.scenario_roles:
        if role.participant_source == "agent_ref":
            profile.participant_scope.require(role.agent_ref)
        elif (
            role.participant_source != "open_admission"
            and role.required
            and sources.get(role.participant_source) is None
        ):
            raise ContextCollabValidationError(
                f"product role {role.name!r} requires {role.participant_source}"
            )


def prepare_product_profile(
    *,
    product_kind: str | None,
    workflow_kind: str | None,
    method_kind: str | None,
    profile_ref: VersionedProfileRef | None,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    catalog: ProductProfileReader,
) -> LegacyProductConfiguration | None:
    """Select an exact installed profile without mutating a creation object."""
    return validate_product_selection(
        product_kind=product_kind,
        workflow_kind=workflow_kind,
        method_kind=method_kind,
        profile_ref=profile_ref,
        principal_ref=principal_ref,
        executor_ref=executor_ref,
        catalog=catalog,
    )


def agent_participant_id(agent: AgentRef, executor: AgentRef | None) -> str:
    """Use one stable identity for fixed role subjects and supplied participants."""
    if agent == executor:
        return "participant_executor_ref"
    return (
        "participant_"
        + quote(agent.agent_type, safe="")
        + ":"
        + quote(agent.agent_id, safe="")
    )


def scenario_participant_id(role: ScenarioRole, executor: AgentRef | None) -> str:
    if role.participant_source == "agent_ref":
        if role.agent_ref is None:
            raise ContextCollabValidationError(
                "agent_ref role requires a typed AgentRef"
            )
        return agent_participant_id(role.agent_ref, executor)
    return "participant_" + role.participant_source


def role_binding_refs(
    collet_id: str,
    *,
    profile: LegacyProductConfiguration,
    executor_ref: AgentRef | None = None,
) -> tuple[str, ...]:
    """Return stable references for the profile's declared role facts."""
    refs: list[str] = []
    for role in profile.scenario_roles:
        if role.participant_source == "open_admission":
            continue
        if role.participant_source == "executor_ref" and executor_ref is None:
            continue
        participant_id = scenario_participant_id(role, executor_ref)
        refs.append(
            "/".join(
                (
                    quote(collet_id, safe=""),
                    "role-bindings",
                    quote(role.l4_role, safe=""),
                    quote(participant_id, safe=""),
                )
            )
        )
    return tuple(refs)


def actor_has_role(
    task: Task | None, actor: PrincipalRef, role: str, profile, events=()
) -> bool:
    """Resolve actual L4 RoleBindings; only legacy history uses Task identities."""
    if task is None:
        return False
    facts = current_participation(events)
    if facts.participants:
        ids = {b.participant_id for b in facts.role_bindings if b.role == role}
        return any(
            p.participant_id in ids
            and (
                p.principal_ref == actor
                or (
                    p.agent_ref is not None
                    and p.agent_ref.agent_type == actor.principal_type
                    and p.agent_ref.agent_id == actor.principal_id
                )
            )
            for p in facts.participants
        )
    # Explicit compatibility for pre-participation event history only.
    scenario_role = next(
        (item for item in profile.scenario_roles if item.l4_role == role), None
    )
    if scenario_role is None:
        return False
    if scenario_role.participant_source in {"agent_ref", "open_admission"}:
        # Literal/deferred roles require actual L4 participation evidence.
        return False
    subject = (
        task.principal_ref
        if scenario_role.participant_source == "principal_ref"
        else task.executor_ref
    )
    if subject is None:
        return False
    if isinstance(subject, PrincipalRef):
        return subject == actor
    return (
        actor.principal_type == subject.agent_type
        and actor.principal_id == subject.agent_id
    )
