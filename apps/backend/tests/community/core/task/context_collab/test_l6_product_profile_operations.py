"""L6 creation authors validated definitions; installation is a separate operation."""

from dataclasses import FrozenInstanceError, fields, replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    ROLE_RESPONSIBLE_USER,
    ROLE_SELECTED_AGENT,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
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
    negotiate,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    ProductProfileCreateCommand,
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenAdmissionPolicy,
    OpenPublicationProfile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
    init_product_profile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.publication import (
    BotSelfApplicationAdmission,
    DesignatedOpenAdmission,
)


@pytest.fixture
def command():
    return ProductProfileCreateCommand(
        ref=VersionedProfileRef("custom_product", 3),
        composition=MethodCompositionTemplate(
            method_kind="Open",
            scope="collet",
            required_methods=(MethodRequirement("Open", "0.2"),),
        ),
        scenario_roles=(
            ScenarioRole(
                "owner", ROLE_RESPONSIBLE_USER, "principal_ref", True, ("accept",)
            ),
            ScenarioRole(
                "assignee", ROLE_SELECTED_AGENT, "executor_ref", True, ("execute",)
            ),
        ),
        domain=DomainProfile(
            VersionedProfileRef("custom_domain", 2),
            (DomainMapping("goal", "L4.Task"),),
        ),
        workflow=WorkflowProfile(
            ref=VersionedProfileRef("custom_workflow", 2),
            state_machine_ref=VersionedProfileRef("custom_machine", 2),
            completion_states=("done",),
            compensation_events=(),
            actions=(
                WorkflowAction(
                    "progress",
                    "task.progress.updated",
                    "Task",
                    ("percent", "note"),
                    required_role=ROLE_SELECTED_AGENT,
                ),
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
    )


def _publication():
    return OpenPublicationProfile(
        admission=OpenAdmissionPolicy(
            applicant_role="assignee",
            accountable_role="owner",
            participation_type=ParticipationType.COLLET_PARTICIPANT,
            capacity=1,
        )
    )


def test_create_returns_the_exact_immutable_definition(command):
    profile = create_product_profile(command)
    assert isinstance(profile, ProductProfileDefinition)
    for field in fields(command):
        assert getattr(profile, field.name) == getattr(command, field.name)
    assert profile.publication is None
    with pytest.raises(FrozenInstanceError):
        profile.ref = VersionedProfileRef("changed", 1)
    with pytest.raises(FrozenInstanceError):
        command.ref = VersionedProfileRef("changed", 1)


def test_creation_does_not_install_or_require_a_catalog(command):
    existing = ProductCatalog(())
    capabilities = existing.capabilities()
    profile = create_product_profile(command)
    again = create_product_profile(command)
    assert again == profile and again is not profile
    assert existing.capabilities() == capabilities
    with pytest.raises(ContextCollabValidationError, match="unknown"):
        existing.get(profile.ref.profile_id, profile.ref.version)
    installed = ProductCatalog((profile,))
    assert installed.get("custom_product", 3) is profile
    with pytest.raises(ContextCollabValidationError, match="duplicate"):
        ProductCatalog((profile, again))


def test_creation_preserves_versions_without_local_capability_negotiation(command):
    command = replace(
        command,
        composition=replace(
            command.composition, required_methods=(MethodRequirement("Open", "999"),)
        ),
    )
    profile = create_product_profile(command)
    assert profile.ref == VersionedProfileRef("custom_product", 3)
    assert profile.workflow.ref == VersionedProfileRef("custom_workflow", 2)
    result = negotiate(profile, ProductCatalog((profile,)).capabilities())
    assert not result.accepted
    assert "method:Open@999" in result.missing_capabilities


@pytest.mark.parametrize(
    "policy",
    [
        DesignatedOpenAdmission(
            "assignee", "owner", ParticipationType.SINGLE_CONTRIBUTION, 1
        ),
        BotSelfApplicationAdmission(
            "assignee", "owner", ParticipationType.COLLET_PARTICIPANT, 1
        ),
    ],
)
def test_creation_keeps_the_authored_product_admission_strategy(command, policy):
    publication = OpenPublicationProfile(admission=policy)
    profile = create_product_profile(replace(command, publication=publication))
    assert profile.publication is publication
    assert profile.require_publication().admission is policy


@pytest.mark.parametrize(
    "invalid, message",
    [
        (
            lambda c: replace(
                c, composition=replace(c.composition, method_kind="Single")
            ),
            "disagree",
        ),
        (
            lambda c: replace(c, composition=replace(c.composition, scope="space")),
            "collet-scoped",
        ),
        (
            lambda c: replace(
                c, compatibility=replace(c.compatibility, downgrade_policy="allow")
            ),
            "downgrade",
        ),
        (
            lambda c: replace(
                c, compatibility=replace(c.compatibility, migration_policy="live")
            ),
            "migration",
        ),
        (lambda c: replace(c, scenario_roles=c.scenario_roles * 2), "duplicate"),
        (
            lambda c: replace(
                c,
                scenario_roles=(
                    c.scenario_roles[0],
                    replace(c.scenario_roles[1], l4_role=ROLE_RESPONSIBLE_USER),
                ),
            ),
            "duplicate",
        ),
        (
            lambda c: replace(
                c,
                scenario_roles=(
                    replace(c.scenario_roles[0], participant_source="resource_ref"),
                    c.scenario_roles[1],
                ),
            ),
            "participant source",
        ),
        (
            lambda c: replace(
                c,
                workflow=replace(
                    c.workflow,
                    actions=(replace(c.workflow.actions[0], required_role="unknown"),),
                ),
            ),
            "undeclared scenario role",
        ),
        (
            lambda c: replace(
                c,
                publication=_publication(),
                scenario_roles=(
                    c.scenario_roles[0],
                    replace(c.scenario_roles[1], required=False),
                ),
            ),
            "required typed subject",
        ),
        (
            lambda c: replace(
                c,
                publication=_publication(),
                composition=MethodCompositionTemplate(
                    "Single",
                    "collet",
                    (MethodRequirement("Single", "0.2"),),
                ),
            ),
            "requires Open Method",
        ),
        (
            lambda c: replace(
                c, publication=_publication(), workflow=replace(c.workflow, actions=())
            ),
            "requires Task/workflow actions",
        ),
    ],
)
def test_create_and_install_enforce_the_same_structural_rules(
    command, invalid, message
):
    command = invalid(command)
    with pytest.raises(ContextCollabValidationError, match=message):
        create_product_profile(command)
    # Direct dataclass construction remains compatible, but cannot bypass validation
    # when such a definition is installed by an older consumer.
    unchecked = ProductProfileDefinition(
        **{field.name: getattr(command, field.name) for field in fields(command)}
    )
    with pytest.raises(ContextCollabValidationError, match=message):
        ProductCatalog((unchecked,))


def test_commission_initialization_uses_l6_without_changing_its_authored_policy():
    profile = init_product_profile()
    assert profile == create_product_profile(TASK_COMMISSION_PROFILE)
    assert profile is not TASK_COMMISSION_PROFILE
    assert profile.publication is TASK_COMMISSION_PROFILE.publication


@pytest.mark.parametrize(
    "omit_domain,omit_workflow", [(True, False), (False, True), (True, True)]
)
def test_domain_and_orchestration_are_independently_optional(
    command, omit_domain, omit_workflow
):
    values = {field.name: getattr(command, field.name) for field in fields(command)}
    if omit_domain:
        del values["domain"]
    if omit_workflow:
        del values["workflow"]
    profile = create_product_profile(ProductProfileCreateCommand(**values))
    assert (profile.domain is None) is omit_domain
    assert (profile.workflow is None) is omit_workflow
    assert ProductCatalog((profile,)).get("custom_product", 3) is profile


def test_standard_contract_uses_existing_g4_machine_and_actions(command):
    from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
        STANDARD_TASK_CONTRACT,
        resolve_event_contract,
    )
    from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
        VERSIONED_MACHINE_REGISTRY,
    )

    profile = create_product_profile(replace(command, workflow=None, domain=None))
    contract = resolve_event_contract(profile)
    assert contract is STANDARD_TASK_CONTRACT
    ref = contract.state_machine_ref
    machine = VERSIONED_MACHINE_REGISTRY[(ref.profile_id, ref.version)]
    assert {a.event_kind for a in contract.actions} == {
        r.event_name for r in machine.transitions
    }
    assert set(contract.completion_states) <= {s.name for s in machine.states}
    assert set(contract.compensation_events) <= {
        r.event_name for r in machine.transitions
    }


@pytest.mark.parametrize(
    "kind", ["unknown", "ambiguous", "missing_executor", "wrong_executor_source"]
)
def test_invalid_task_contract_fails_at_authoring_and_installation(command, kind):
    from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
        STANDARD_TASK_CONTRACT_REF,
    )

    if kind == "ambiguous":
        command = replace(command, task_contract_ref=STANDARD_TASK_CONTRACT_REF)
    elif kind == "unknown":
        command = replace(
            command,
            workflow=None,
            task_contract_ref=VersionedProfileRef("standard_task", 999),
        )
    else:
        assignee = replace(
            command.scenario_roles[1],
            required=kind != "missing_executor",
            participant_source="principal_ref"
            if kind == "wrong_executor_source"
            else "executor_ref",
        )
        command = replace(
            command, workflow=None, scenario_roles=(command.scenario_roles[0], assignee)
        )
    with pytest.raises(ContextCollabValidationError):
        create_product_profile(command)
    unchecked = ProductProfileDefinition(
        **{f.name: getattr(command, f.name) for f in fields(command)}
    )
    with pytest.raises(ContextCollabValidationError):
        ProductCatalog((unchecked,))


def test_explicit_standard_task_contract_matches_the_fixed_default(command):
    from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
        STANDARD_TASK_CONTRACT_REF,
        resolve_event_contract,
    )

    implicit = create_product_profile(replace(command, workflow=None))
    explicit = create_product_profile(
        replace(command, workflow=None, task_contract_ref=STANDARD_TASK_CONTRACT_REF)
    )
    assert resolve_event_contract(explicit) is resolve_event_contract(implicit)
    assert ProductCatalog((explicit,)).get("custom_product", 3) is explicit
