"""L6 ProductProfile creation: construct and validate, without installation or I/O."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    OpenMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    BotSelfApplicationAdmission,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductProfileDefinition,
    ScenarioRole,
    MethodCompositionTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductProfileCreateCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.validation import (
    validate_product_profile,
)


def create_product_profile(
    command: ProductProfileCreateCommand,
) -> ProductProfileDefinition:
    """Return a validated immutable definition, or raise ContextCollabValidationError.

    This is a pure authoring operation, not an authenticated runtime write API.
    Preserve the supplied IDs, versions and publication strategy exactly. Validate
    the same structural rules as ProductCatalog; installation uniqueness and local
    capability negotiation remain separate. Do not mutate any existing catalog,
    create a Collet/Task/Method binding, or execute publication/admission operations.
    """
    if not isinstance(command.scenario_roles, tuple) or any(
        not isinstance(role, ScenarioRole) for role in command.scenario_roles
    ):
        raise ContextCollabValidationError(
            "scenario_roles must be a tuple of ScenarioRole"
        )
    definition = ProductProfileDefinition(
        ref=command.ref,
        participant_scope=command.participant_scope,
        composition=command.composition,
        scenario_roles=command.scenario_roles
        or default_scenario_roles(command.composition),
        domain=command.domain,
        workflow=command.workflow,
        task_contract_ref=command.task_contract_ref,
        governance=command.governance,
        compatibility=command.compatibility,
    )
    validate_product_profile(definition)
    return definition


def default_scenario_roles(
    composition: MethodCompositionTemplate,
) -> tuple[ScenarioRole, ...]:
    """Standard ownership/execution roles, with no preselected Open applicant.

    Open policy names are preserved. Stage roles come from published opportunity
    requirements and actual admission, not a fixed Agent roster in the product.
    Explicit authored roles bypass this default and retain their validation.
    """
    policies = [
        d.policy
        for d in composition.required_methods
        if isinstance(d.policy, OpenMethodPolicy)
    ]
    if len(policies) > 1:
        raise ContextCollabValidationError(
            "default roles require at most one Open policy"
        )
    admission = policies[0].publication.admission if policies else None
    return (
        ScenarioRole(
            admission.accountable_role if admission else "owner",
            "responsible_user",
            "principal_ref",
            True,
            ("coordinate", "accept"),
        ),
        ScenarioRole(
            admission.applicant_role if admission else "submitter",
            "selected-agent",
            "open_admission"
            if admission and not isinstance(admission, BotSelfApplicationAdmission)
            else "executor_ref",
            True,
            ("submit_result",),
        ),
    )
