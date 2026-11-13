"""Installed task_commission v1: explicit product semantics, no runtime engine."""

from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    ROLE_RESPONSIBLE_USER,
    ROLE_SELECTED_AGENT,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationType,
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
    WorkflowAction,
    WorkflowProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenPublicationProfile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.publication import (
    DesignatedOpenAdmission,
)

# New behavior/topology needs a new ref; existing instances never select latest.
# Executor-only progress below enforces the existing v1 assignee responsibility
# (authorization bug fix), not a profile migration.
TASK_COMMISSION_PROFILE_REF = VersionedProfileRef("task_commission", 1)

TASK_COMMISSION_PROFILE = ProductProfileDefinition(
    ref=TASK_COMMISSION_PROFILE_REF,
    composition=MethodCompositionTemplate(
        method_kind="Open",
        scope="collet",
        required_methods=(MethodRequirement("Open", "0.2"),),
    ),
    scenario_roles=(
        ScenarioRole(
            "owner",
            ROLE_RESPONSIBLE_USER,
            "principal_ref",
            True,
            ("define_goal", "accept", "reject", "cancel"),
        ),
        ScenarioRole(
            "assignee",
            ROLE_SELECTED_AGENT,
            "executor_ref",
            True,
            ("execute", "report_progress", "submit"),
        ),
    ),
    domain=DomainProfile(
        VersionedProfileRef("task_commission.domain", 1),
        (
            DomainMapping("collaboration", "L4.Collet"),
            DomainMapping("goal", "L4.Task"),
            DomainMapping("posting", "L4.Message"),
            DomainMapping("deliverable", "L4.Artifact"),
            DomainMapping("resource", "L3.Resource"),
        ),
    ),
    workflow=WorkflowProfile(
        ref=VersionedProfileRef("task_commission_goal", 1),
        state_machine_ref=VersionedProfileRef("task_commission_goal", 1),
        completion_states=("done",),
        compensation_events=("task.rejected", "task.cancelled"),
        strict_control_events=True,
        executor_submission_only=True,
        public_events_only=True,
        actions=(
            WorkflowAction("start_task", "task.started", "Task", ("note",)),
            WorkflowAction(
                "report_progress",
                "task.progress.updated",
                "Task",
                ("note", "percent"),
                required_role=ROLE_SELECTED_AGENT,
            ),
            WorkflowAction(
                "submit_task",
                "task.submitted",
                "Task",
                ("note", "artifacts"),
            ),
            WorkflowAction("accept_task", "task.accepted", "Task", ("note",)),
            WorkflowAction("reject_task", "task.rejected", "Task", ("note",)),
            WorkflowAction("cancel_task", "task.cancelled", "Task", ("note",)),
        ),
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
    publication=OpenPublicationProfile(
        admission=DesignatedOpenAdmission(
            applicant_role="assignee",
            accountable_role="owner",
            participation_type=ParticipationType.SINGLE_CONTRIBUTION,
            capacity=1,
        ),
    ),
)
