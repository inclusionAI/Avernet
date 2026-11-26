"""Command-only review resolves applications within exactly one Collet/axes."""

import inspect
from dataclasses import fields, replace

import pytest

from agentclaw.community.core.task.context_collab import method_service_api
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenAdmitCommand,
    OpenMethodOpsServiceProtocol,
    OpenScreenCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.service import (
    OpenMethodService,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    StoreAxes,
)
from tests.community.core.task.context_collab.open_profile_support import (
    AXES,
    COLLET_ID,
    active_open_binding,
    make_actor,
    make_open_service,
    opportunity_command,
    submitted_application,
)


@pytest.fixture(params=["screen", "admit"])
def operation(request):
    return request.param


@pytest.fixture
def review(operation):
    store, _, lifecycle, service = make_open_service()
    binding_id = active_open_binding(lifecycle)
    service.publish_opportunity(
        opportunity_command(participation_type=ParticipationType.COLLET_PARTICIPANT)
    )
    app = submitted_application(service)
    command_type = OpenScreenCommand if operation == "screen" else OpenAdmitCommand
    if operation == "admit":
        service.screen(OpenScreenCommand(app, make_actor(), COLLET_ID))
    binding = store.get_binding(AXES, binding_id, COLLET_ID)
    command = command_type(app, make_actor(), COLLET_ID, reason="policy://review")
    return store, binding, service, command


def _assert_no_review_write(store, binding, before):
    assert store.get_application(
        AXES, before.application_id, binding.method_binding_id, COLLET_ID
    ) == before
    assert store.list_decisions(AXES, binding.method_binding_id, COLLET_ID) == []
    assert store.list_participant_bindings(AXES, binding.method_binding_id, COLLET_ID) == []


@pytest.mark.parametrize(
    "operation,command_type", [("screen", OpenScreenCommand), ("admit", OpenAdmitCommand)]
)
def test_complete_command_is_the_only_argument(operation, command_type):
    for owner in (OpenMethodOpsServiceProtocol, OpenMethodService):
        assert tuple(inspect.signature(getattr(owner, operation)).parameters) == (
            "self", "command",
        )
    assert getattr(method_service_api, command_type.__name__) is command_type
    names = [field.name for field in fields(command_type)]
    assert names[:3] == ["application_ref", "context", "collect_id"]
    assert not set(names) & {"binding_id", "method_binding_id", "admitted_scope_ref"}
    args = dict(application_ref="app_1", context=make_actor(), collect_id=COLLET_ID)
    for name in args:
        with pytest.raises(TypeError, match=name):
            command_type(**{key: value for key, value in args.items() if key != name})
    with pytest.raises(TypeError, match="admitted_scope_ref"):
        command_type(**args, admitted_scope_ref="foreign_collet")


def test_resolves_application_not_first_or_top_level_binding(review, operation):
    store, binding, service, command = review
    store.upsert_binding(
        AXES, replace(binding, method_binding_id="mb_unrelated", parent_scope_ref=None)
    )
    result = getattr(service, operation)(command=command)
    assert (result.collab_context_id, result.method_binding_id) == (
        COLLET_ID, binding.method_binding_id,
    )
    assert result.state == ("SCREENING" if operation == "screen" else "ADMITTED")
    row = store.get_application(AXES, command.application_ref, binding.method_binding_id, COLLET_ID)
    assert row.transitioned_by == command.context.actor.principal_id
    assert row.transition_reason == command.reason
    assert row.transitioned_from == ("SUBMITTED" if operation == "screen" else "SCREENING")
    assert store.list_decisions(AXES, "mb_unrelated", COLLET_ID) == []
    if operation == "admit":
        [decision] = store.list_decisions(AXES, binding.method_binding_id, COLLET_ID)
        [participant] = store.list_participant_bindings(AXES, binding.method_binding_id, COLLET_ID)
        assert decision.admitted_scope_ref == participant.scope_ref == COLLET_ID


@pytest.mark.parametrize("field", ["collect_id", "application_ref"])
@pytest.mark.parametrize("value", ["", "   "])
def test_empty_required_identifiers_fail_without_write(review, operation, field, value):
    store, binding, service, command = review
    before = store.get_application(AXES, command.application_ref, binding.method_binding_id, COLLET_ID)
    with pytest.raises(ContextCollabValidationError, match=f"{field} must not be empty"):
        getattr(service, operation)(replace(command, **{field: value}))
    _assert_no_review_write(store, binding, before)


@pytest.mark.parametrize("field", ["collect_id", "application_ref"])
def test_missing_or_foreign_reference_cannot_review_original(review, operation, field):
    store, binding, service, command = review
    before = store.get_application(AXES, command.application_ref, binding.method_binding_id, COLLET_ID)
    with pytest.raises(ContextCollabNotFoundError):
        getattr(service, operation)(replace(command, **{field: "foreign"}))
    _assert_no_review_write(store, binding, before)


@pytest.mark.parametrize("state", [BindingState.ACTIVE, BindingState.SUSPENDED])
@pytest.mark.parametrize("method", ["Open", "Group"])
def test_duplicate_application_ids_fail_closed_including_inactive(
    review, operation, state, method
):
    store, binding, service, command = review
    before = store.get_application(AXES, command.application_ref, binding.method_binding_id, COLLET_ID)
    duplicate = replace(
        binding, method_binding_id="mb_duplicate", state=state,
        method_kind=method, method_names=(method,),
    )
    store.upsert_binding(AXES, duplicate)
    store.insert_application(AXES, duplicate.method_binding_id, before, COLLET_ID)
    with pytest.raises(ContextCollabValidationError, match="ambiguous application_ref"):
        getattr(service, operation)(command)
    _assert_no_review_write(store, binding, before)
    assert store.get_application(AXES, command.application_ref, "mb_duplicate", COLLET_ID) == before
    assert store.list_decisions(AXES, "mb_duplicate", COLLET_ID) == []


@pytest.mark.parametrize("partition", ["collet", "tenant", "env"])
def test_other_collet_and_axes_do_not_compete(review, operation, partition):
    store, binding, service, command = review
    axes, collet_id = AXES, COLLET_ID
    if partition == "collet":
        collet_id = "other_collet"
    elif partition == "tenant":
        axes = StoreAxes("other_tenant", AXES.env)
    else:
        axes = StoreAxes(AXES.avernet_tenant, "other_env")
    row = store.get_application(AXES, command.application_ref, binding.method_binding_id, COLLET_ID)
    store.upsert_binding(axes, replace(binding, collab_context_id=collet_id))
    store.insert_application(axes, binding.method_binding_id, row, collet_id)
    assert getattr(service, operation)(command).accepted
    assert store.get_application(axes, command.application_ref, binding.method_binding_id, collet_id) == row
    assert store.list_decisions(axes, binding.method_binding_id, collet_id) == []


@pytest.mark.parametrize("change,match", [
    ({"state": BindingState.SUSPENDED}, "ACTIVE"),
    ({"method_kind": "Group", "method_names": ("Group",)}, "does not declare Open"),
])
def test_resolved_binding_still_requires_active_open(review, operation, change, match):
    store, binding, service, command = review
    before = store.get_application(AXES, command.application_ref, binding.method_binding_id, COLLET_ID)
    store.replace_binding(AXES, replace(binding, **change))
    with pytest.raises(ContextCollabValidationError, match=match):
        getattr(service, operation)(command)
    _assert_no_review_write(store, binding, before)


@pytest.mark.parametrize("principal", ["", "u_stranger"])
def test_trusted_context_is_still_authorized_by_g1(review, operation, principal):
    store, binding, service, command = review
    before = store.get_application(AXES, command.application_ref, binding.method_binding_id, COLLET_ID)
    with pytest.raises(ContextCollabValidationError):
        getattr(service, operation)(replace(command, context=make_actor(principal)))
    _assert_no_review_write(store, binding, before)


def test_storage_failures_propagate(review, operation, monkeypatch):
    store, binding, service, command = review

    def fail(*args):
        raise RuntimeError("review storage unavailable")

    method = "update_application_state" if operation == "screen" else "insert_decision"
    monkeypatch.setattr(store, method, fail)
    with pytest.raises(RuntimeError, match="review storage unavailable"):
        getattr(service, operation)(command)
    row = store.get_application(AXES, command.application_ref, binding.method_binding_id, COLLET_ID)
    assert row.state.value == ("SUBMITTED" if operation == "screen" else "SCREENING")
    assert store.list_decisions(AXES, binding.method_binding_id, COLLET_ID) == []
