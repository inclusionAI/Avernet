"""Immutable, code-owned product definitions; no mutable runtime registry or database."""

from __future__ import annotations

from types import MappingProxyType

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
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
from agentclaw.community.core.task.context_collab.l6_products.validation import (
    validate_product_profile,
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
            validate_product_profile(definition)
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
