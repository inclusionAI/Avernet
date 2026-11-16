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
from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenAdmissionPolicy,
    OpenPublicationProfile,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenMethodOpsServiceProtocol,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
    init_product_profile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    AssigneeAdmissionCommand,
)
from agentclaw.community.di.modules import task_commission_method_ops_adapter as adapter
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG
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
    publication = profile.require_publication()
    assert publication.admission.applicant_role == "assignee"
    assert publication.admission.accountable_role == "owner"
    assert publication.admission.strategy == "designated_bot_self_application"
    assert publication.admission.capacity == 1
    assert (
        publication.admission.participation_type
        is ParticipationType.COLLET_PARTICIPANT
    )
    assert publication.dispatch_after is ApplicationState.ADMITTED
    assert publication.partial_failure == "propagate_without_rollback"
    assert profile.composition.method_kind == "Open"  # No inferred Single binding.

    machine_ref = profile.workflow.state_machine_ref
    machine = VERSIONED_MACHINE_REGISTRY[(machine_ref.profile_id, machine_ref.version)]
    assert {action.event_kind for action in profile.workflow.actions} == {
        transition.event_name for transition in machine.transitions
    }
    assert profile.workflow.action("submit_task").payload_fields == (
        "note",
        "artifacts",
    )
    assert profile.workflow.action("report_progress").payload_fields == (
        "note",
        "percent",
    )
    with pytest.raises(ContextCollabValidationError, match="no declared action"):
        profile.workflow.action("invented_action")
    with pytest.raises(ContextCollabValidationError, match="duplicate workflow"):
        replace(profile.workflow, actions=profile.workflow.actions * 2)


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
    assert facts["workflow_kind"] == profile.workflow.ref.profile_id
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
    assert selected.workflow.ref.version == 1
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
        replace(TASK_COMMISSION_PROFILE.require_publication().admission, **overrides)


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
        replace(profile, composition=PRODUCT_CATALOG.get("team_todo", 1).composition),
        replace(profile, workflow=replace(profile.workflow, actions=())),
    ):
        with pytest.raises(ContextCollabValidationError):
            ProductCatalog((bad,))


def _adapter_case(*, admitted_state="ADMITTED"):
    # A separately installed exact version proves the adapter does not use v1
    # constants or select "latest". No production catalog is mutated.
    v1 = TASK_COMMISSION_PROFILE
    publication = v1.require_publication()
    v2 = replace(
        v1,
        ref=VersionedProfileRef("task_commission", 2),
        publication=replace(
            publication, admission=replace(publication.admission, capacity=2)
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
    policy = profile.require_publication().admission
    assert tuple(call[0] for call in ops.mock_calls) == policy.steps
    args = ops.publish_opportunity.call_args.args
    assert len(args) == 1
    assert args[0].collab_context_id == command.collab_context_id
    assert args[0].context is actor
    assert args[0].capacity == policy.capacity == 2
    assert args[0].participation_type == policy.participation_type.value
    assert args[0].scope_ref == command.collab_context_id
    application = ops.submit_application.call_args.args[3]
    assert application.applicant_agent_ref == ASSIGNEE
    assert application.accountable_principal_ref == actor.actor.principal_id
    assert all(call.args[-1] is actor for call in ops.mock_calls[1:])
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
        scenario_roles=(
            replace(owner, participant_source="executor_ref"),
            replace(assignee, participant_source="principal_ref"),
        ),
        publication=OpenPublicationProfile(
            OpenAdmissionPolicy("assignee", "owner", ParticipationType.GROUP_MEMBER, 3)
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


def test_initial_profile_uses_self_application_without_version_bump():
    legacy = TASK_COMMISSION_PROFILE
    profile = init_product_profile()
    assert PRODUCT_CATALOG.get("task_commission", 1) == profile
    assert profile.ref == VersionedProfileRef("task_commission", 1)
    assert profile.composition.method_kind == "Open"
    assert {
        item.method: item.version for item in profile.composition.required_methods
    } == {
        "Open": "0.2",
    }
    policy = profile.require_publication().admission
    assert policy.strategy == "designated_bot_self_application"
    assert policy.participation_type is ParticipationType.COLLET_PARTICIPANT
    assert policy.capacity == 1
    assert profile.workflow == legacy.workflow
    assert legacy.composition.method_kind == "Open"
    assert (
        legacy.require_publication().admission.participation_type
        is ParticipationType.SINGLE_CONTRIBUTION
    )


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
    policy = init_product_profile().require_publication().admission
    with pytest.raises(ContextCollabValidationError):
        replace(policy, **overrides)
