"""Versioned task_commission definitions; explicit semantics, no runtime engine."""

from dataclasses import replace

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
    WorkflowProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    ProductProfileCreateCommand,
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    STANDARD_TASK_CONTRACT,
)
from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenPublicationProfile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.publication import (
    BotSelfApplicationAdmission,
    DesignatedOpenAdmission,
)

# The installed self-application behavior intentionally reuses the
# task_commission@1 reference; existing instances never select latest.
# Executor-only progress below enforces the task_commission@1 assignee
# responsibility (authorization bug fix), not a profile migration.
TASK_COMMISSION_PROFILE_REF = VersionedProfileRef("task_commission", 1)

TASK_COMMISSION_PROFILE = ProductProfileCreateCommand(
    # 版本
    ref=TASK_COMMISSION_PROFILE_REF,
    # 绑定L5的method，这里是OPEN方法
    composition=MethodCompositionTemplate(
        method_kind="Open",
        scope="collet",
        required_methods=(MethodRequirement("Open", "0.2"),),
    ),
    # 角色信息
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
    # g1-g4的定义
    governance=GovernanceRequirements(
        policy="g1.structural/1",
        reliability="g2.optimistic-version/1",
        observation="g3.event-projection/1",
        consistency="g4.product-contract/1",
    ),
    # 能力需求声明
    compatibility=CompatibilityRequirements(
        accs_revision="0.2",
        required_layers=("L1", "L2", "L3", "L4", "L5"),
        required_extensions=(),
        optional_extensions=(),
        downgrade_policy="reject_missing_required",
        migration_policy="new_instances_only",
    ),
    # 产品发布参与机会时使用的规则
    publication=OpenPublicationProfile(
        admission=DesignatedOpenAdmission(
            applicant_role="assignee",
            accountable_role="owner",
            participation_type=ParticipationType.SINGLE_CONTRIBUTION,
            capacity=1,
        ),
    ),
)

SELF_SERVICE_COMMISSION_PROFILE_REF = VersionedProfileRef("task_commission", 1)


def init_product_profile() -> ProductProfileDefinition:
    """Create the authored task-commission definition through the L6 operation.

    Keep its exact version and publication policy; DI explicitly installs the
    returned definition. Creating a profile does not mutate a runtime catalog.
    """
    return create_product_profile(TASK_COMMISSION_PROFILE)
