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
    LegacyProductConfiguration,
)


def product_binding_refs(
    collet_id: str, profile: LegacyProductConfiguration
) -> tuple[str, ...]:
    if profile.composition.relations:
        return tuple(
            profile_binding_id(collet_id, method.profile.method_kind)
            for method in profile.composition.required_methods
        )
    return (top_level_binding_id(collet_id),)


def product_method_binding_ref(
    collet_id: str, profile: LegacyProductConfiguration, method: str
) -> str:
    if method not in {
        item.profile.method_kind for item in profile.composition.required_methods
    }:
        raise ContextCollabValidationError(
            f"product does not require Method {method!r}"
        )
    if profile.composition.relations:
        return profile_binding_id(collet_id, method)
    return top_level_binding_id(collet_id)


def product_execution_binding_ref(
    collet_id: str, profile: LegacyProductConfiguration
) -> str:
    methods = {
        item.profile.method_kind for item in profile.composition.required_methods
    }
    return product_method_binding_ref(
        collet_id, profile, "Single" if "Single" in methods else "Open"
    )


def product_relation_id(collet_id: str, relation: MethodRelationTemplate) -> str:
    return (
        f"mce_{collet_id}_{relation.source_method.lower()}_"
        f"{relation.relation.value}_{relation.target_method.lower()}"
    )
