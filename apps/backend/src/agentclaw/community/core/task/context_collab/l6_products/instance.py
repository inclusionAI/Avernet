"""Durable L6 selection record, stored beside ContextSpec in its existing JSON column."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    validate_product_spec,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    GovernanceRequirements,
    MethodRequirement,
    NegotiationResult,
    VersionedProfileRef,
    negotiate,
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
    # Each RoleBinding is identified by (Collet, role, participant), no new role authority.
    role_binding_refs: tuple[str, ...]
    governance: GovernanceRequirements
    negotiation: NegotiationResult
    creation_event_ref: str

    def to_dict(self) -> dict:
        # JSON-normalized form (tuple -> list) is stable across persistence round trips.
        return json.loads(json.dumps(asdict(self)))

    @classmethod
    def from_dict(cls, data: dict) -> ProductInstance:
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
            raise ContextCollabValidationError(
                f"invalid ProductInstance: {error}"
            ) from error


def _instance(
    spec: ContextSpec,
    collet_id: str,
    event_id: str,
    selection: NegotiationResult,
    catalog: ProductCatalog,
) -> ProductInstance:
    profile = validate_product_spec(spec, catalog)
    if profile is None or spec.product_profile_version is None:
        raise ContextCollabValidationError(
            "ProductInstance requires a pinned profile version"
        )
    return ProductInstance(
        instance_id=f"pi_{collet_id}",
        collet_ref=collet_id,
        profile_ref=profile.ref,
        workflow_ref=profile.workflow.ref,
        domain_ref=profile.domain.ref,
        method_binding_refs=(top_level_binding_id(collet_id),),
        method_profiles=profile.composition.required_methods,
        role_binding_refs=tuple(
            role_binding_ref(collet_id, item) for item in spec.role_bindings
        ),
        governance=profile.governance,
        negotiation=selection,
        creation_event_ref=event_id,
    )


def instantiate_product(
    spec: ContextSpec, collet_id: str, event_id: str, catalog: ProductCatalog
) -> ProductInstance:
    profile = validate_product_spec(spec, catalog)
    if profile is None or spec.product_profile_version is None:
        raise ContextCollabValidationError(
            "new ProductInstance requires a pinned profile version"
        )
    selection = negotiate(profile, catalog.capabilities())
    if not selection.accepted:
        raise ContextCollabValidationError(
            f"product negotiation rejected: {selection.missing_capabilities}"
        )
    return _instance(spec, collet_id, event_id, selection, catalog)


def decode_product_instance(
    data: dict, spec: ContextSpec, collet_id: str, catalog: ProductCatalog
) -> ProductInstance | None:
    """Validate historical facts, never renegotiate with today's available capabilities."""
    raw = data.get("product_instance")
    if raw is None:
        if spec.product_profile_version is not None:
            raise ContextCollabValidationError(
                "pinned product is missing ProductInstance"
            )
        return None
    instance = ProductInstance.from_dict(raw)
    selection = instance.negotiation
    profile = validate_product_spec(spec, catalog)
    if (
        profile is None
        or selection.selected_profile != profile.ref
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
    optional.update(
        f"extension:{item}" for item in profile.compatibility.optional_extensions
    )
    if not set(selection.omitted_optional_capabilities) <= optional:
        raise ContextCollabValidationError(
            "unknown optional capability in persisted product negotiation"
        )
    if instance != _instance(
        spec, collet_id, instance.creation_event_ref, selection, catalog
    ):
        raise ContextCollabValidationError(
            "ProductInstance does not match persisted product bindings"
        )
    return instance
