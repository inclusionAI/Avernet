"""Concrete v1 definitions for existing generic/discussion products; no registration."""

from __future__ import annotations


from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    ROLE_RESPONSIBLE_USER,
    ROLE_SELECTED_AGENT,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    parse_method_kinds,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    CompatibilityRequirements,
    DomainMapping,
    DomainProfile,
    GovernanceRequirements,
    MethodCompositionTemplate,
    MethodRequirement,
    ProductProfileDefinition,
    ScenarioRole,
    VersionedProfileRef,
    WorkflowProfile,
)


def _definition(
    product: str,
    workflow: str,
    method: str,
    *,
    goal: bool,
    executor_required: bool = False,
    message_parent_only: bool = False,
) -> ProductProfileDefinition:
    # Versions are literals, never derived from the installed "latest" Method profile.
    return ProductProfileDefinition(
        ref=VersionedProfileRef(product, 1),
        composition=MethodCompositionTemplate(
            method_kind=method,
            scope="collet",
            required_methods=tuple(
                MethodRequirement(item.value, "0.2")
                for item in parse_method_kinds(method)
            ),
        ),
        scenario_roles=(
            ScenarioRole(
                "owner",
                ROLE_RESPONSIBLE_USER,
                "principal_ref",
                True,
                ("goal", "accept", "cancel"),
            ),
            ScenarioRole(
                "assignee",
                ROLE_SELECTED_AGENT,
                "executor_ref",
                executor_required,
                ("execute", "submit"),
            ),
        ),
        domain=DomainProfile(
            VersionedProfileRef(f"{product}.domain", 1),
            (
                DomainMapping("collaboration", "L4.Collet"),
                DomainMapping("goal", "L4.Task"),
                DomainMapping("posting", "L4.Message"),
                DomainMapping("deliverable", "L4.Artifact"),
                DomainMapping("resource", "L3.Resource"),
            ),
        ),
        workflow=WorkflowProfile(
            ref=VersionedProfileRef(workflow, 1),
            state_machine_ref=VersionedProfileRef(workflow, 1) if goal else None,
            completion_states=("done",) if goal else (),
            compensation_events=("task.rejected", "task.cancelled") if goal else (),
            message_parent_only=message_parent_only,
            public_events_only=message_parent_only,
        ),
        governance=GovernanceRequirements(
            policy="g1.structural/1",
            reliability="g2.optimistic-version/1",
            observation="g3.event-projection/1",
            consistency="g4.product-contract/1",
        ),
        compatibility=CompatibilityRequirements(
            accs_revision="0.2",
            required_layers=("L2", "L3", "L4", "L5"),
            required_extensions=(),
            optional_extensions=(),
            downgrade_policy="reject_missing_required",
            migration_policy="new_instances_only",
        ),
    )


# Compatibility IDs retain their current topology. A changed topology requires a new version.
LEGACY_PRODUCT_PROFILES = (
    _definition(
        "team_todo", "team_todo_execution", "Single", goal=True, executor_required=True
    ),
    _definition(
        "public_discussion", "forum_public_discussion", "Open + Group", goal=False
    ),
    _definition(
        "bbs_public_discussion",
        "bbs_public_discussion",
        "Open + Group",
        goal=False,
        message_parent_only=True,
    ),
    _definition("bbs_help_request", "bbs_help_request_goal", "Open + Group", goal=True),
)
