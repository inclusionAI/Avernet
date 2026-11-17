"""Pure resolution of a pinned product's explicit Method binding references."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    profile_binding_id,
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodRelationTemplate,
    ProductProfileDefinition,
)


def product_binding_refs(
    collet_id: str, profile: ProductProfileDefinition
) -> tuple[str, ...]:
    if profile.composition.relations:
        return tuple(
            profile_binding_id(collet_id, method.method_kind)
            for method in profile.composition.required_methods
        )
    return (top_level_binding_id(collet_id),)


def product_method_binding_ref(
    collet_id: str, profile: ProductProfileDefinition, method: str
) -> str:
    if method not in {
        item.method_kind for item in profile.composition.required_methods
    }:
        raise ContextCollabValidationError(
            f"product does not require Method {method!r}"
        )
    if profile.composition.relations:
        return profile_binding_id(collet_id, method)
    return top_level_binding_id(collet_id)


def product_execution_binding_ref(
    collet_id: str, profile: ProductProfileDefinition
) -> str:
    methods = {item.method_kind for item in profile.composition.required_methods}
    return product_method_binding_ref(
        collet_id, profile, "Single" if "Single" in methods else "Open"
    )


def product_relation_id(collet_id: str, relation: MethodRelationTemplate) -> str:
    return (
        f"mce_{collet_id}_{relation.parent_method.lower()}_"
        f"{relation.relation.value}_{relation.child_method.lower()}"
    )
