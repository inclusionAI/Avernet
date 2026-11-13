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
    prepare_product_spec,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenMethodOpsServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenAdmissionPolicy,
    OpenPublicationProfile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    AssigneeAdmissionCommand,
)
from agentclaw.community.di.modules import task_commission_method_ops_adapter as adapter
from tests.community.core.task.context_collab.products.test_task_commission_publish import (
    ASSIGNEE,
    _actor,
    _publish_command,
    _service,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG


def test_commission_has_a_complete_declarative_publication_and_work_contract():
    profile = PRODUCT_CATALOG.get("task_commission", 1)
    assert profile is TASK_COMMISSION_PROFILE
    publication = profile.require_publication()
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
    spec = repo.create_calls[0].context_spec
    profile = TASK_COMMISSION_PROFILE
    assert spec.product_kind == profile.ref.profile_id
    assert spec.product_profile_version == profile.ref.version
    assert spec.method_kind == profile.composition.method_kind
    assert spec.workflow_kind == profile.workflow.ref.profile_id
    assert spec.workflow_profile_version == profile.workflow.ref.version
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
    pinned = repo.create_calls[0].context_spec
    unpinned = replace(
        pinned,
        product_profile_version=None,
        workflow_profile_version=None,
        participants=(),
        role_bindings=(),
    )
    v2 = replace(TASK_COMMISSION_PROFILE, ref=VersionedProfileRef("task_commission", 2))
    catalog = ProductCatalog((TASK_COMMISSION_PROFILE, v2))
    selected = prepare_product_spec(unpinned, catalog, selected_profile=v2.ref)
    assert selected.product_profile_version == 2
    assert selected.workflow_profile_version == 1
    assert prepare_product_spec(unpinned, catalog).product_profile_version == 1
    with pytest.raises(ContextCollabValidationError, match="unpinned"):
        prepare_product_spec(pinned, catalog, selected_profile=v2.ref)
    with pytest.raises(ContextCollabValidationError, match="does not match"):
        prepare_product_spec(
            unpinned, catalog, selected_profile=VersionedProfileRef("other", 1)
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
    assert args[:2] == (command.collab_context_id, command.method_binding_ref)
    assert args[2].capacity == policy.capacity == 2
    assert args[2].participation_type == policy.participation_type.value
    assert args[2].scope_ref == command.collab_context_id
    application = ops.submit_application.call_args.args[3]
    assert application.applicant_agent_ref == ASSIGNEE
    assert application.accountable_principal_ref == actor.actor.principal_id
    assert all(call.args[-1] is actor for call in ops.mock_calls)
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
