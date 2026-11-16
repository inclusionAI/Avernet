"""Shared structural validation for authored and installed L6 product definitions."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    parse_method_kinds,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
)


def validate_product_profile(definition: ProductProfileDefinition) -> None:
    """Reject unsupported composition/role/publication contracts without installing."""
    methods = definition.composition.required_methods
    if tuple(item.method for item in methods) != tuple(
        item.value for item in parse_method_kinds(definition.composition.method_kind)
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
    for action in definition.workflow.actions:
        if action.required_role is not None and action.required_role not in roles:
            raise ContextCollabValidationError(
                "workflow action requires an undeclared scenario role"
            )
    if definition.publication is not None:
        policy = definition.publication.admission
        policy.validate_roles(definition.scenario_roles)
        if "Open" not in {method.method for method in methods}:
            raise ContextCollabValidationError("Open publication requires Open Method")
        if not definition.workflow.actions:
            raise ContextCollabValidationError("publication requires workflow actions")
