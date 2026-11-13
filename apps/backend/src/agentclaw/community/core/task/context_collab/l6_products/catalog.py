"""Immutable, code-owned product definitions; no mutable runtime registry or database."""

from __future__ import annotations

from types import MappingProxyType

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    parse_method_kinds,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    METHOD_PROFILE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodRequirement,
    ProductCapabilities,
    ProductProfileDefinition,
    VersionedProfileRef,
)


class ProductCatalog:
    """Exact version lookup; product teams may supply definitions without extending the ID enum."""

    def __init__(self, definitions: tuple[ProductProfileDefinition, ...]) -> None:
        indexed: dict[VersionedProfileRef, ProductProfileDefinition] = {}
        for definition in definitions:
            if definition.ref in indexed:
                raise ContextCollabValidationError(
                    f"duplicate product profile: {definition.ref}"
                )
            methods = definition.composition.required_methods
            if tuple(item.method for item in methods) != tuple(
                item.value
                for item in parse_method_kinds(definition.composition.method_kind)
            ):
                raise ContextCollabValidationError(
                    "composition and required Method profiles disagree"
                )
            if definition.composition.scope != "collet":
                raise ContextCollabValidationError(
                    "only collet-scoped top-level templates are supported"
                )
            if definition.compatibility.downgrade_policy != "reject_missing_required":
                raise ContextCollabValidationError("unsupported downgrade policy")
            if definition.compatibility.migration_policy != "new_instances_only":
                raise ContextCollabValidationError("unsupported migration policy")
            names = [item.name for item in definition.scenario_roles]
            roles = [item.l4_role for item in definition.scenario_roles]
            if len(set(names)) != len(names) or len(set(roles)) != len(roles):
                raise ContextCollabValidationError("duplicate scenario role mapping")
            for role in definition.scenario_roles:
                if role.participant_source not in {"principal_ref", "executor_ref"}:
                    raise ContextCollabValidationError(
                        "unsupported scenario role participant source"
                    )
            if definition.publication is not None:
                policy = definition.publication.admission
                policy.validate_roles(definition.scenario_roles)
                if "Open" not in {method.method for method in methods}:
                    raise ContextCollabValidationError(
                        "Open publication requires Open Method"
                    )
                if not definition.workflow.actions:
                    raise ContextCollabValidationError(
                        "publication requires workflow actions"
                    )
            indexed[definition.ref] = definition
        self._definitions = MappingProxyType(indexed)

    def get(self, product: str, version: int) -> ProductProfileDefinition:
        ref = VersionedProfileRef(product, version)
        try:
            return self._definitions[ref]
        except KeyError:
            raise ContextCollabValidationError(
                f"unknown product_kind/profile version: {product!r}@{version}"
            ) from None

    def capabilities(self) -> ProductCapabilities:
        """Installed local capabilities, not a fabricated remote Agent offer."""
        return ProductCapabilities(
            profiles=tuple(self._definitions),
            methods=tuple(
                MethodRequirement(name, item.profile_version)
                for name, item in METHOD_PROFILE_REGISTRY.items()
            ),
            accs_revisions=("0.2",),
            layers=("L2", "L3", "L4", "L5"),
            extensions=(),
            governance=(
                "g1.structural/1",
                "g2.optimistic-version/1",
                "g3.event-projection/1",
                "g4.product-contract/1",
            ),
        )
