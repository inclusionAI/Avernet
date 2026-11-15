"""Durable L6 product selection facts stored with the creation facts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    role_binding_refs,
    validate_product_selection,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    GovernanceRequirements,
    MethodRequirement,
    NegotiationResult,
    ProductProfileDefinition,
    VersionedProfileRef,
    negotiate,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_profile_ref,
    decode_creation_facts,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)


@dataclass(frozen=True)
class ProductInstance:
    """References only: current Task/Method state remains authoritative in L4/L5."""

    instance_id: str
    collet_ref: str
    profile_ref: VersionedProfileRef
    workflow_ref: VersionedProfileRef
    domain_ref: VersionedProfileRef
    method_binding_refs: tuple[str, ...]
    method_profiles: tuple[MethodRequirement, ...]
    role_binding_refs: tuple[str, ...]
    governance: GovernanceRequirements
    negotiation: NegotiationResult
    creation_event_ref: str

    def to_dict(self) -> dict:
        return json.loads(json.dumps(asdict(self)))

    @classmethod
    def from_dict(cls, data: dict) -> "ProductInstance":
        try:
            result = data["negotiation"]
            if type(result["accepted"]) is not bool:
                raise ValueError("negotiation.accepted must be a boolean")
            instance = cls(
                instance_id=data["instance_id"],
                collet_ref=data["collet_ref"],
                profile_ref=VersionedProfileRef(**data["profile_ref"]),
                workflow_ref=VersionedProfileRef(**data["workflow_ref"]),
                domain_ref=VersionedProfileRef(**data["domain_ref"]),
                method_binding_refs=tuple(data["method_binding_refs"]),
                method_profiles=tuple(
                    MethodRequirement(**item) for item in data["method_profiles"]
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
            )
            if instance.to_dict() != data or not instance.negotiation.accepted:
                raise ValueError("invalid product instance selection")
            return instance
        except (KeyError, TypeError, ValueError) as error:
            raise ContextCollabValidationError(f"invalid ProductInstance: {error}") from error


def _resolve_profile(
    facts: dict,
    *,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    catalog: ProductCatalog,
) -> ProductProfileDefinition:
    normalized = decode_creation_facts(facts)
    profile = validate_product_selection(
        product_kind=normalized["product_kind"],
        workflow_kind=normalized["workflow_kind"],
        method_kind=normalized["method_kind"],
        profile_ref=creation_profile_ref(normalized),
        principal_ref=principal_ref,
        executor_ref=executor_ref,
        catalog=catalog,
    )
    if profile is None or creation_profile_ref(normalized) is None:
        raise ContextCollabValidationError(
            "ProductInstance requires a pinned profile version"
        )
    return profile


def _instance(
    *,
    facts: dict,
    collet_id: str,
    event_id: str,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    profile: ProductProfileDefinition,
    selection: NegotiationResult,
) -> ProductInstance:
    return ProductInstance(
        instance_id=f"pi_{collet_id}",
        collet_ref=collet_id,
        profile_ref=profile.ref,
        workflow_ref=profile.workflow.ref,
        domain_ref=profile.domain.ref,
        method_binding_refs=(top_level_binding_id(collet_id),),
        method_profiles=profile.composition.required_methods,
        role_binding_refs=role_binding_refs(collet_id, profile=profile),
        governance=profile.governance,
        negotiation=selection,
        creation_event_ref=event_id,
    )


def instantiate_product(
    *,
    facts: dict,
    collet_id: str,
    event_id: str,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    catalog: ProductCatalog,
) -> ProductInstance | None:
    normalized = decode_creation_facts(facts)
    if normalized["product_kind"] is None:
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
    event_id: str,
    principal_ref: PrincipalRef,
    executor_ref: AgentRef | None,
    catalog: ProductCatalog,
) -> ProductInstance | None:
    """Validate historical facts; never renegotiate with today's capabilities."""
    normalized = decode_creation_facts(facts)
    raw = normalized.get("product_instance")
    profile_ref = creation_profile_ref(normalized)
    if raw is None:
        if profile_ref is not None:
            raise ContextCollabValidationError("pinned product is missing ProductInstance")
        return None
    instance = ProductInstance.from_dict(raw)
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
        f"method:{item.method}@{item.version}"
        for item in profile.composition.optional_methods
    }
    optional.update(f"extension:{item}" for item in profile.compatibility.optional_extensions)
    if not set(selection.omitted_optional_capabilities) <= optional:
        raise ContextCollabValidationError(
            "unknown optional capability in persisted product negotiation"
        )
    expected = _instance(
        facts=normalized,
        collet_id=collet_id,
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
