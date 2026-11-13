"""Product selection and ScenarioRole -> actual L4 facts; no IO or parallel state."""

from __future__ import annotations

from dataclasses import replace

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
    RoleBinding,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
    VersionedProfileRef,
    negotiate,
)


def product_definition(
    spec: ContextSpec, catalog: ProductCatalog
) -> ProductProfileDefinition | None:
    if spec.product_kind is None:
        if (
            spec.product_profile_version is not None
            or spec.workflow_profile_version is not None
        ):
            raise ContextCollabValidationError("product version requires product_kind")
        return None
    # Explicit legacy rule: unversioned rows mean v1, NEVER the newest definition.
    profile = catalog.get(
        spec.product_kind,
        1 if spec.product_profile_version is None else spec.product_profile_version,
    )
    if spec.workflow_kind != profile.workflow.ref.profile_id:
        raise ContextCollabValidationError(
            f"workflow_kind {spec.workflow_kind!r} is incompatible with product {spec.product_kind!r}"
        )
    if spec.workflow_profile_version is not None:
        VersionedProfileRef(
            profile.workflow.ref.profile_id, spec.workflow_profile_version
        )
    if (
        1 if spec.workflow_profile_version is None else spec.workflow_profile_version
    ) != profile.workflow.ref.version:
        raise ContextCollabValidationError("incompatible workflow profile version")
    if spec.method_kind != profile.composition.method_kind:
        raise ContextCollabValidationError(
            f"method_kind {spec.method_kind!r} is incompatible with product {spec.product_kind!r}"
        )
    for role in profile.scenario_roles:
        if role.required and getattr(spec, role.participant_source) is None:
            raise ContextCollabValidationError(
                f"product role {role.name!r} requires {role.participant_source}"
            )
    return profile


def _role_facts(
    spec: ContextSpec, profile: ProductProfileDefinition
) -> tuple[tuple[Participant, ...], tuple[RoleBinding, ...]]:
    participants: dict[str, Participant] = {}
    bindings: list[RoleBinding] = []
    for role in profile.scenario_roles:
        subject = getattr(spec, role.participant_source)
        if subject is None:
            continue
        participant = Participant(
            participant_id=f"participant_{role.participant_source}",
            principal_ref=subject
            if role.participant_source == "principal_ref"
            else None,
            agent_ref=subject if role.participant_source == "executor_ref" else None,
        )
        participants[participant.participant_id] = participant
        bindings.append(
            RoleBinding(role=role.l4_role, participant_id=participant.participant_id)
        )
    return tuple(participants.values()), tuple(bindings)


def validate_product_spec(
    spec: ContextSpec, catalog: ProductCatalog
) -> ProductProfileDefinition | None:
    """Validate pinned facts without negotiating against today's installed capabilities."""
    profile = product_definition(spec, catalog)
    if profile is not None and spec.product_profile_version is not None:
        participants, bindings = _role_facts(spec, profile)
        if (
            spec.workflow_profile_version != profile.workflow.ref.version
            or spec.participants != participants
            or spec.role_bindings != bindings
        ):
            raise ContextCollabValidationError(
                "pinned product RoleBinding/version facts do not match ScenarioRole mappings"
            )
    return profile


def prepare_product_spec(
    spec: ContextSpec,
    catalog: ProductCatalog,
    *,
    selected_profile: VersionedProfileRef | None = None,
) -> ContextSpec:
    """Negotiate a new selection, never migrate or repair existing pinned facts.

    Generic callers retain v1 selection. Product use cases can explicitly select
    an installed version before any runtime objects are written.
    """
    if selected_profile is not None:
        if (
            spec.product_profile_version is not None
            or spec.workflow_profile_version is not None
            or spec.participants
            or spec.role_bindings
        ):
            raise ContextCollabValidationError(
                "explicit selection requires an unpinned spec"
            )
        if spec.product_kind != selected_profile.profile_id:
            raise ContextCollabValidationError(
                "selected profile does not match product_kind"
            )
        selected = catalog.get(selected_profile.profile_id, selected_profile.version)
        participants, bindings = _role_facts(spec, selected)
        spec = replace(
            spec,
            product_profile_version=selected.ref.version,
            workflow_profile_version=selected.workflow.ref.version,
            participants=participants,
            role_bindings=bindings,
        )
    profile = validate_product_spec(spec, catalog)
    if profile is None:
        return spec
    result = negotiate(profile, catalog.capabilities())
    if not result.accepted:
        raise ContextCollabValidationError(
            f"product negotiation rejected: {result.missing_capabilities}"
        )
    participants, bindings = _role_facts(spec, profile)
    return replace(
        spec,
        product_profile_version=profile.ref.version,
        workflow_profile_version=profile.workflow.ref.version,
        participants=participants,
        role_bindings=bindings,
    )


def actor_has_role(spec: ContextSpec, actor: PrincipalRef, role: str) -> bool:
    # Actual L4 facts only: this identity helper does not select product profiles.
    participants, bindings = spec.participants, spec.role_bindings
    participant_ids = {item.participant_id for item in bindings if item.role == role}
    return any(
        item.participant_id in participant_ids
        and (
            item.principal_ref == actor
            or (
                item.agent_ref is not None
                and actor.principal_type == item.agent_ref.agent_type
                and actor.principal_id == item.agent_ref.agent_id
            )
        )
        for item in participants
    )
