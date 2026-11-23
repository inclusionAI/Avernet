"""Durable L6 product selection facts stored with the creation facts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    MethodProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    product_definition,
    role_binding_refs,
    validate_role_sources,
)
from agentclaw.community.core.task.context_collab.l6_products.profile_reader import (
    ProductProfileReader,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    GovernanceRequirements,
    NegotiationResult,
    ProductProfileDefinition,
    VersionedProfileRef,
    negotiate,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_profile_ref,
    decode_creation_facts,
)
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    resolve_event_contract,
)
from agentclaw.community.core.task.context_collab.l6_products.topology import (
    product_binding_refs,
)


@dataclass(frozen=True)
class ProductInstance:
    """References only: current Task/Method state remains authoritative in L4/L5."""

    instance_id: str
    collet_ref: str
    root_task_ref: str | None
    profile_ref: VersionedProfileRef
    workflow_ref: VersionedProfileRef | None
    domain_ref: VersionedProfileRef | None
    method_binding_refs: tuple[str, ...]
    method_profiles: tuple[MethodProfileRef, ...]
    role_binding_refs: tuple[str, ...]
    governance: GovernanceRequirements
    negotiation: NegotiationResult
    creation_event_ref: str
    # Absent only for historical WorkflowProfile-owned Task semantics.
    task_contract_ref: VersionedProfileRef | None = None
    participant_scope: ParticipantScope = ParticipantScope()

    def to_dict(self) -> dict:
        data = json.loads(json.dumps(asdict(self)))
        data["participant_scope"] = self.participant_scope.to_dict()
        if self.task_contract_ref is None:
            del data["task_contract_ref"]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ProductInstance":
        try:
            data = {"participant_scope": ParticipantScope().to_dict(), **data}
            root_ref = data["root_task_ref"]
            if root_ref is not None and (
                not isinstance(root_ref, str) or not root_ref.strip()
            ):
                raise ValueError("root_task_ref must be a non-empty Task reference")
            result = data["negotiation"]
            if type(result["accepted"]) is not bool:
                raise ValueError("negotiation.accepted must be a boolean")
            instance = cls(
                instance_id=data["instance_id"],
                participant_scope=ParticipantScope.from_dict(data["participant_scope"]),
                collet_ref=data["collet_ref"],
                root_task_ref=data["root_task_ref"],
                profile_ref=VersionedProfileRef(**data["profile_ref"]),
                workflow_ref=(
                    VersionedProfileRef(**data["workflow_ref"])
                    if data["workflow_ref"] is not None
                    else None
                ),
                domain_ref=(
                    VersionedProfileRef(**data["domain_ref"])
                    if data["domain_ref"] is not None
                    else None
                ),
                method_binding_refs=tuple(data["method_binding_refs"]),
                method_profiles=tuple(
                    MethodProfileRef(**item) for item in data["method_profiles"]
                ),
                role_binding_refs=tuple(data["role_binding_refs"]),
                governance=GovernanceRequirements(**data["governance"]),
                negotiation=NegotiationResult(
                    selected_profile=VersionedProfileRef(**result["selected_profile"]),
                    accepted=result["accepted"],
                    missing_capabilities=tuple(result["missing_capabilities"]),
                    omitted_optional_capabilities=tuple(
                        result["omitted_optional_capabilities"]
                    ),
                    reason=result["reason"],
                    mode=result["mode"],
                ),
                creation_event_ref=data["creation_event_ref"],
                task_contract_ref=(
                    VersionedProfileRef(**data["task_contract_ref"])
                    if "task_contract_ref" in data
                    else None
                ),
            )
            if instance.to_dict() != data or not instance.negotiation.accepted:
                raise ValueError("invalid product instance selection")
            return instance
        except (KeyError, TypeError, ValueError) as error:
            raise ContextCollabValidationError(
                f"invalid ProductInstance: {error}"
            ) from error


def _resolve_profile(
    facts: dict,
    *,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    catalog: ProductProfileReader,
) -> ProductProfileDefinition:
    normalized = decode_creation_facts(facts)
    profile = product_definition(
        product_kind=normalized["product_kind"],
        workflow_kind=normalized["workflow_kind"],
        method_kind=normalized["method_kind"],
        profile_ref=creation_profile_ref(normalized),
        catalog=catalog,
    )
    if profile is None or creation_profile_ref(normalized) is None:
        raise ContextCollabValidationError(
            "ProductInstance requires a pinned profile version"
        )
    validate_role_sources(profile, principal_ref, executor_ref)
    return profile


def _instance(
    *,
    facts: dict,
    collet_id: str,
    root_task_id: str | None,
    event_id: str,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    profile: ProductProfileDefinition,
    selection: NegotiationResult,
) -> ProductInstance:
    if root_task_id is not None and (not root_task_id or root_task_id == collet_id):
        raise ContextCollabValidationError(
            "ProductInstance requires an independent root Task"
        )
    return ProductInstance(
        instance_id=f"pi_{collet_id}",
        collet_ref=collet_id,
        root_task_ref=root_task_id,
        profile_ref=profile.ref,
        workflow_ref=profile.workflow.ref if profile.workflow else None,
        domain_ref=profile.domain.ref if profile.domain else None,
        task_contract_ref=(
            resolve_event_contract(profile).ref if profile.workflow is None else None
        ),
        method_binding_refs=product_binding_refs(collet_id, profile),
        method_profiles=tuple(
            item.profile.ref for item in profile.composition.required_methods
        ),
        role_binding_refs=role_binding_refs(
            collet_id, profile=profile, executor_ref=executor_ref
        ),
        participant_scope=profile.participant_scope,
        governance=profile.governance,
        negotiation=selection,
        creation_event_ref=event_id,
    )


def instantiate_product(
    *,
    facts: dict,
    collet_id: str,
    root_task_id: str | None,
    event_id: str,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    catalog: ProductProfileReader,
) -> ProductInstance | None:
    normalized = decode_creation_facts(facts)
    if normalized.get("product_kind") is None:
        return None
    profile = _resolve_profile(
        normalized,
        principal_ref=principal_ref,
        executor_ref=executor_ref,
        catalog=catalog,
    )
    selection = negotiate(profile, catalog.capabilities())
    if not selection.accepted:
        raise ContextCollabValidationError(
            f"product negotiation rejected: {selection.missing_capabilities}"
        )
    return _instance(
        facts=normalized,
        collet_id=collet_id,
        root_task_id=root_task_id,
        event_id=event_id,
        principal_ref=principal_ref,
        executor_ref=executor_ref,
        profile=profile,
        selection=selection,
    )


def decode_product_instance(
    facts: dict,
    *,
    collet_id: str,
    root_task_id: str | None,
    event_id: str,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    catalog: ProductProfileReader,
) -> ProductInstance | None:
    """Validate historical facts; never renegotiate with today's capabilities."""
    normalized = decode_creation_facts(facts)
    raw = normalized.get("product_instance")
    profile_ref = creation_profile_ref(normalized)
    if raw is None:
        if profile_ref is not None:
            raise ContextCollabValidationError(
                "pinned product is missing ProductInstance"
            )
        return None
    if not isinstance(raw, dict):
        raise ContextCollabValidationError(
            "invalid ProductInstance: expected an object"
        )
    # Historical JSON predates the explicit root reference. Resolve it only
    # from the authoritative L4 snapshot; do not rewrite the stored record.
    # A pre-start installation stores null, not a placeholder Task identity.
    # Once Work exists, resolve that live reference from the authoritative L4 row.
    instance = ProductInstance.from_dict({"root_task_ref": root_task_id, **raw})
    if instance.root_task_ref is None and root_task_id is not None:
        if normalized["objective"] is not None:
            raise ContextCollabValidationError(
                "ProductInstance is missing its root Task reference"
            )
        instance = replace(instance, root_task_ref=root_task_id)
    profile = _resolve_profile(
        normalized,
        principal_ref=principal_ref,
        executor_ref=executor_ref,
        catalog=catalog,
    )
    selection = instance.negotiation
    if (
        selection.selected_profile != profile.ref
        or selection.missing_capabilities
        or selection.mode != "local_exact_match"
        or not isinstance(selection.reason, str)
        or not selection.reason.strip()
    ):
        raise ContextCollabValidationError("invalid persisted product negotiation")
    optional = {
        f"method:{item.profile.method_kind}@{item.profile.profile_version}"
        for item in profile.composition.optional_methods
    }
    optional.update(
        f"extension:{item}" for item in profile.compatibility.optional_extensions
    )
    if not set(selection.omitted_optional_capabilities) <= optional:
        raise ContextCollabValidationError(
            "unknown optional capability in persisted product negotiation"
        )
    expected = _instance(
        facts=normalized,
        collet_id=collet_id,
        root_task_id=root_task_id,
        event_id=event_id,
        principal_ref=principal_ref,
        executor_ref=executor_ref,
        profile=profile,
        selection=selection,
    )
    if instance != expected or instance.creation_event_ref != event_id:
        raise ContextCollabValidationError(
            "ProductInstance does not match persisted product bindings"
        )
    return instance
