"""The product and its bounded Open adapter consume the selected L6 contract."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    VERSIONED_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    prepare_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.method_policies import (
    OpenMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenAdmissionPolicy,
    OpenPublicationProfile,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenMethodOpsServiceProtocol,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
    OPEN_SINGLE_COMMISSION_PROFILE,
    init_product_profile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    AssigneeAdmissionCommand,
)
from agentclaw.community.di.modules import task_commission_method_ops_adapter as adapter
from tests.community.core.task.context_collab.product_catalog import (
    PRODUCT_CATALOG,
    standard_task_workflow,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    STANDARD_TASK_CONTRACT_REF,
    resolve_event_contract,
)
from tests.community.core.task.context_collab.products.test_task_commission_publish import (
    ASSIGNEE,
    _actor,
    _publish_command,
    _service,
)


PRINCIPAL = PrincipalRef("human", "u_1001")


def test_commission_has_a_complete_declarative_publication_and_work_contract():
    profile = PRODUCT_CATALOG.get("task_commission", 1)
    assert profile == init_product_profile()
    publication = profile.composition.require_policy("Open").publication
    assert publication.admission.applicant_role == "assignee"
    assert publication.admission.accountable_role == "owner"
    assert publication.admission.strategy == "publisher_mediated"
    assert publication.admission.capacity == 1
    assert (
        publication.admission.participation_type
        is ParticipationType.SINGLE_CONTRIBUTION
    )
    assert publication.dispatch_after is ApplicationState.ADMITTED
    assert publication.partial_failure == "propagate_without_rollback"
    assert profile.composition.method_kind == "Open"  # No inferred Single binding.

    assert profile.workflow is None
    contract = resolve_event_contract(profile)
    assert contract.ref == STANDARD_TASK_CONTRACT_REF
    machine_ref = contract.state_machine_ref
    machine = VERSIONED_MACHINE_REGISTRY[(machine_ref.profile_id, machine_ref.version)]
    assert {action.event_kind for action in contract.actions} == {
        transition.event_name for transition in machine.transitions
    }
    assert contract.action("submit_task").payload_fields == (
        "note",
        "artifacts",
    )
    assert contract.action("report_progress").payload_fields == (
        "note",
        "percent",
        "progress",
        "error_msg",
        "reason",
        "task_id",
    )
    with pytest.raises(ContextCollabValidationError, match="no declared action"):
        contract.action("invented_action")
    with pytest.raises(ContextCollabValidationError, match="duplicate Task"):
        replace(contract, actions=contract.actions * 2)


def test_publish_uses_selected_profile_and_passes_actual_binding_to_method_adapter():
    service, repo, store = _service()
    created = service.publish_task(_publish_command(), _actor())
    facts = repo.create_calls[0].creation_facts
    profile = TASK_COMMISSION_PROFILE
    assert facts["product_kind"] == profile.ref.profile_id
    assert facts["product_profile_ref"] == {
        "profile_id": profile.ref.profile_id,
        "version": profile.ref.version,
    }
    assert facts["method_kind"] == profile.composition.method_kind
    assert facts["workflow_kind"] is None
    from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
        current_axes,
    )

    [binding] = store.list_bindings(current_axes(), created.collet_id)
    assert binding.method_binding_id == top_level_binding_id(created.collet_id)
    assert binding.profile_versions == {"Open": "0.2"}
    assert len(binding.role_binding_refs) == 2


def test_explicit_profile_selection_pins_exact_version_and_never_migrates():
    service, repo, _ = _service()
    service.publish_task(_publish_command(), _actor())
    facts = repo.create_calls[0].creation_facts
    v2 = replace(TASK_COMMISSION_PROFILE, ref=VersionedProfileRef("task_commission", 2))
    catalog = ProductCatalog((TASK_COMMISSION_PROFILE, v2))
    selected = prepare_product_profile(
        product_kind=facts["product_kind"],
        workflow_kind=facts["workflow_kind"],
        method_kind=facts["method_kind"],
        profile_ref=v2.ref,
        principal_ref=PRINCIPAL,
        executor_ref=AgentRef("bot", ASSIGNEE),
        catalog=catalog,
    )
    assert selected.ref.version == 2
    assert selected.workflow is None
    assert resolve_event_contract(selected).ref == STANDARD_TASK_CONTRACT_REF
    default = prepare_product_profile(
        product_kind=facts["product_kind"],
        workflow_kind=facts["workflow_kind"],
        method_kind=facts["method_kind"],
        profile_ref=None,
        principal_ref=PRINCIPAL,
        executor_ref=AgentRef("bot", ASSIGNEE),
        catalog=catalog,
    )
    assert default.ref.version == 1
    with pytest.raises(ContextCollabValidationError, match="does not match"):
        prepare_product_profile(
            product_kind=facts["product_kind"],
            workflow_kind=facts["workflow_kind"],
            method_kind=facts["method_kind"],
            profile_ref=VersionedProfileRef("other", 1),
            principal_ref=PRINCIPAL,
            executor_ref=AgentRef("bot", ASSIGNEE),
            catalog=catalog,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"strategy": "impersonate_agent"},
        {"steps": ("admit",)},
        {"participation_type": ParticipationType.GROUP_MEMBER},
        {"capacity": 0},
        {"capacity": True},
    ],
)
def test_unsupported_publication_policy_rejected_before_execution(overrides):
    with pytest.raises(ContextCollabValidationError):
        replace(
            TASK_COMMISSION_PROFILE.composition.require_policy("Open").publication.admission,
            **overrides,
        )


def test_catalog_rejects_publication_without_required_typed_roles_or_method():
    profile = TASK_COMMISSION_PROFILE
    owner, assignee = profile.scenario_roles
    for bad in (
        replace(profile, scenario_roles=(owner,)),
        replace(profile, scenario_roles=(owner, replace(assignee, required=False))),
        replace(
            profile,
            scenario_roles=(
                owner,
                replace(assignee, participant_source="principal_ref"),
            ),
        ),
        replace(
            profile,
            composition=replace(
                PRODUCT_CATALOG.get("team_todo", 1).composition,
                policies=profile.composition.policies,
            ),
        ),
        replace(profile, workflow=replace(standard_task_workflow(), actions=())),
    ):
        with pytest.raises(ContextCollabValidationError):
            ProductCatalog((bad,))


def _adapter_case(*, admitted_state="ADMITTED"):
    # A separately installed exact version proves the adapter does not use v1
    # constants or select "latest". No production catalog is mutated.
    v1 = TASK_COMMISSION_PROFILE
    publication = v1.composition.require_policy("Open").publication
    v2 = replace(
        v1,
        ref=VersionedProfileRef("task_commission", 2),
        composition=replace(
            v1.composition,
            policies=(
                OpenMethodPolicy(
                    publication=replace(
                        publication, admission=replace(publication.admission, capacity=2)
                    ),
                ),
            ),
        ),
    )
    ops = Mock(spec=OpenMethodOpsServiceProtocol)
    ops.publish_opportunity.return_value = SimpleNamespace(object_id="opp")
    ops.submit_application.return_value = SimpleNamespace(object_id="app")
    ops.admit.return_value = SimpleNamespace(state=admitted_state)
    command = AssigneeAdmissionCommand(
        collab_context_id="collet_test",
        assignee_agent_id=ASSIGNEE,
        accountable_principal_id="u_1001",
        product_profile_ref=v2.ref,
        method_binding_ref=top_level_binding_id("collet_test"),
    )
    return (
        adapter.TaskCommissionOpenMethodAdapter(
            ops, product_catalog=ProductCatalog((v1, v2))
        ),
        ops,
        command,
        v2,
    )


def test_adapter_executes_exact_selected_policy_and_declared_steps():
    executor, ops, command, profile = _adapter_case()
    actor = _actor()
    receipt = executor.admit_designated_assignee(command, actor)
    policy = profile.composition.require_policy("Open").publication.admission
    assert tuple(call[0] for call in ops.mock_calls) == policy.steps
    args = ops.publish_opportunity.call_args.args
    assert len(args) == 1
    assert args[0].collet_id == command.collab_context_id
    assert args[0].actor_context is actor
    assert args[0].capacity == policy.capacity == 2
    assert args[0].participation_type is policy.participation_type
    application = ops.submit_application.call_args.args[0]
    assert application.applicant_agent_ref == ASSIGNEE
    assert application.actor_context is actor
    assert application.opportunity_ref == "opp"
    assert all(call.kwargs["command"].context is actor for call in ops.mock_calls[2:])
    assert receipt.application_id == "app"
    assert receipt.state == "ADMITTED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("method_binding_ref", "different_binding"),
        ("accountable_principal_id", "someone_else"),
        ("product_profile_ref", VersionedProfileRef("task_commission", 999)),
    ],
)
def test_adapter_rejects_invalid_selection_or_identity_before_side_effects(
    field, value
):
    executor, ops, command, _ = _adapter_case()
    with pytest.raises(ContextCollabValidationError):
        executor.admit_designated_assignee(replace(command, **{field: value}), _actor())
    assert ops.mock_calls == []


def test_adapter_refuses_nonadmitted_lower_layer_result():
    executor, _, command, _ = _adapter_case(admitted_state="SCREENING")
    with pytest.raises(ContextCollabValidationError, match="requires an ADMITTED"):
        executor.admit_designated_assignee(command, _actor())


def test_generic_open_policy_does_not_encode_commission_role_sources():
    profile = TASK_COMMISSION_PROFILE
    owner, assignee = profile.scenario_roles
    generic = replace(
        profile,
        workflow=standard_task_workflow(),
        scenario_roles=(
            replace(owner, participant_source="executor_ref"),
            replace(assignee, participant_source="principal_ref"),
        ),
        composition=replace(
            profile.composition,
            policies=(
                OpenMethodPolicy(
                    publication=OpenPublicationProfile(
                        OpenAdmissionPolicy(
                            "assignee", "owner", ParticipationType.GROUP_MEMBER, 3
                        )
                    ),
                ),
            ),
        ),
    )
    assert ProductCatalog((generic,)).get("task_commission", 1) is generic
    _, _, command, _ = _adapter_case()
    command = replace(command, product_profile_ref=generic.ref)
    ops = Mock(spec=OpenMethodOpsServiceProtocol)
    executor = adapter.TaskCommissionOpenMethodAdapter(ops, ProductCatalog((generic,)))
    with pytest.raises(ContextCollabValidationError, match="unsupported designated"):
        executor.admit_designated_assignee(command, _actor())
    assert ops.mock_calls == []


def test_open_single_is_an_explicit_successor_without_changing_v1():
    legacy = init_product_profile()
    profile = OPEN_SINGLE_COMMISSION_PROFILE
    assert PRODUCT_CATALOG.get("task_commission", 1) == legacy
    assert PRODUCT_CATALOG.get("task_commission", 2) == profile
    assert profile.ref == VersionedProfileRef("task_commission", 2)
    assert profile.composition.method_kind == "Open + Single"
    assert {
        item.method_kind: item.profile_version
        for item in profile.composition.required_methods
    } == {"Open": "0.2", "Single": "0.2"}
    [relation] = profile.composition.relations
    assert (relation.parent_method, relation.child_method, relation.relation.value) == (
        "Single", "Open", "support"
    )
    policy = profile.composition.require_policy("Open").publication.admission
    assert policy.strategy == "designated_bot_self_application"
    assert policy.participation_type is ParticipationType.COLLET_PARTICIPANT
    assert policy.capacity == 1
    assert profile.workflow is legacy.workflow is None
    assert legacy.composition.method_kind == "Open"
    legacy_policy = legacy.composition.require_policy("Open").publication.admission
    assert legacy_policy.strategy == "publisher_mediated"
    assert legacy_policy.participation_type is ParticipationType.SINGLE_CONTRIBUTION


@pytest.mark.parametrize(
    "overrides",
    [
        {"strategy": "publisher_mediated"},
        {"participation_type": ParticipationType.SINGLE_CONTRIBUTION},
        {"capacity": 2},
        {"participation_type": ParticipationType.GROUP_MEMBER},
        {"participation_type": ParticipationType.SWARM_ALLOCATION},
    ],
)
def test_self_application_policy_rejects_incompatible_semantics(overrides):
    policy = OPEN_SINGLE_COMMISSION_PROFILE.composition.require_policy("Open").publication.admission
    with pytest.raises(ContextCollabValidationError):
        replace(policy, **overrides)
