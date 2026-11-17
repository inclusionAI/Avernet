"""Binding-free Open publication resolves within the caller's Collet/store axes."""

import inspect
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from typing import get_type_hints
from unittest.mock import Mock

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
    OpenOpportunityOpCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open import (
    base as open_base,
    opportunity as opportunity_ops,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.service import (
    OpenMethodService,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
    StoreAxes,
)
from tests.community.core.task.context_collab.open_profile_support import (
    AXES,
    COLLET_ID,
    active_open_binding,
    make_actor,
    make_open_service,
    opportunity_command,
)


@pytest.fixture
def publication():
    store, repo, lifecycle, service = make_open_service()
    binding_id = active_open_binding(lifecycle)
    return store, repo, store.get_binding(AXES, binding_id, COLLET_ID), service


def test_protocol_and_implementation_accept_only_a_complete_command():
    for owner in (OpenMethodOpsServiceProtocol, OpenMethodService):
        assert tuple(inspect.signature(owner.publish_opportunity).parameters) == (
            "self", "command",
        )
    command = opportunity_command()
    field_names = tuple(field.name for field in fields(OpenOpportunityCommand))
    assert field_names[:2] == ("collet_id", "actor_context")
    names = set(field_names)
    assert not names & {
        "binding_id", "method_binding_id", "collab_context_id", "context",
        "scope_ref", "opens_at",
    }
    for required in ("collet_id", "actor_context", "participation_type"):
        kwargs = {name: getattr(command, name) for name in names - {required}}
        with pytest.raises(TypeError, match=required):
            OpenOpportunityCommand(**kwargs)


def test_opportunity_command_requires_the_existing_participation_enum():
    annotation = get_type_hints(OpenOpportunityCommand)["participation_type"]
    assert annotation is ParticipationType
    assert opportunity_command().participation_type is ParticipationType.GROUP_MEMBER


@pytest.mark.parametrize("participation_type", list(ParticipationType))
def test_publish_preserves_participation_enum_and_string_wire_shape(
    publication, participation_type
):
    store, _, binding, service = publication
    command = opportunity_command(participation_type=participation_type)
    result = service.publish_opportunity(command)
    row = store.get_opportunity(AXES, result.object_id, binding.method_binding_id, COLLET_ID)
    assert row.participation_type is participation_type
    wire_value = row.to_dict()["participation_type"]
    assert type(wire_value) is str
    assert wire_value == participation_type.value
    assert service.publish_opportunity(command) == result


@pytest.mark.parametrize("method_kind", ["Open", "Open + Group"])
@pytest.mark.parametrize("top_level", [False, True])
def test_publish_resolves_unique_open_binding_and_preserves_audit(
    publication, method_kind, top_level
):
    store, _, original, service = publication
    binding = replace(
        original,
        method_kind=method_kind,
        method_names=tuple(method_kind.split(" + ")),
        profile_versions={name: "1" for name in method_kind.split(" + ")},
        parent_scope_ref=None if top_level else "scope://nested",
    )
    store.replace_binding(AXES, binding)
    command = opportunity_command(reason="owner publication")
    result = service.publish_opportunity(command)
    assert result.method_binding_id == binding.method_binding_id
    row = store.get_opportunity(AXES, result.object_id, result.method_binding_id, COLLET_ID)
    assert row.transitioned_by == command.actor_context.actor.principal_id
    assert row.transition_reason == command.reason
    # Participation scope is deliberately not the binding's parent scope.
    assert row.scope_ref == command.collet_id != binding.parent_scope_ref
    assert service.publish_opportunity(command) == result
    assert len(store.list_opportunities(AXES, binding.method_binding_id, COLLET_ID)) == 1


def test_multiple_active_open_bindings_fail_without_top_level_preference(publication):
    store, _, binding, service = publication
    top = replace(
        binding, method_binding_id=top_level_binding_id(COLLET_ID), parent_scope_ref=None
    )
    store.upsert_binding(AXES, top)
    with pytest.raises(ContextCollabValidationError, match="exactly one ACTIVE.*found 2"):
        service.publish_opportunity(opportunity_command())
    for candidate in (binding, top):
        assert store.list_opportunities(AXES, candidate.method_binding_id, COLLET_ID) == []


@pytest.mark.parametrize(
    "state", [BindingState.BOUND, BindingState.SUSPENDED, BindingState.TERMINATED]
)
def test_inactive_open_and_active_non_open_bindings_do_not_compete(publication, state):
    store, _, binding, service = publication
    store.upsert_binding(
        AXES, replace(binding, method_binding_id="inactive_open", state=state)
    )
    store.upsert_binding(
        AXES,
        replace(
            binding, method_binding_id="group_only", method_kind="Group",
            method_names=("Group",), profile_versions={"Group": "1"},
        ),
    )
    result = service.publish_opportunity(opportunity_command())
    assert result.method_binding_id == binding.method_binding_id
    store.replace_binding(AXES, replace(binding, state=state))
    with pytest.raises(ContextCollabValidationError, match="exactly one ACTIVE.*found 0"):
        service.publish_opportunity(opportunity_command(opportunity_id="opp_no_active"))
    assert len(store.list_opportunities(AXES, binding.method_binding_id, COLLET_ID)) == 1


@pytest.mark.parametrize(
    "axes,collet",
    [
        (StoreAxes("other_tenant", AXES.env), COLLET_ID),
        (StoreAxes(AXES.avernet_tenant, "other_env"), COLLET_ID),
        (AXES, "other_collet"),
    ],
)
def test_resolution_never_reads_or_writes_another_partition(publication, axes, collet):
    store, _, binding, service = publication
    foreign = replace(binding, collab_context_id=collet)
    store.upsert_binding(axes, foreign)
    # The foreign ACTIVE Open binding cannot make the local resolution ambiguous.
    result = service.publish_opportunity(opportunity_command())
    assert result.collab_context_id == COLLET_ID
    assert store.list_opportunities(axes, foreign.method_binding_id, collet) == []
    store.replace_binding(AXES, replace(binding, state=BindingState.SUSPENDED))
    # Nor can it substitute for a missing local ACTIVE candidate.
    with pytest.raises(ContextCollabValidationError, match="found 0"):
        service.publish_opportunity(opportunity_command(opportunity_id="opp_missing"))


def test_command_actor_is_authorized_even_for_an_idempotent_replay(publication):
    store, _, binding, service = publication
    command = opportunity_command()
    for published in (False, True):
        if published:
            service.publish_opportunity(command)
        with pytest.raises(ContextCollabValidationError, match="stranger"):
            service.publish_opportunity(
                replace(command, actor_context=make_actor("stranger"))
            )
        rows = store.list_opportunities(AXES, binding.method_binding_id, COLLET_ID)
        assert len(rows) == int(published)


@pytest.mark.parametrize("collet", ["", " ", "unknown_collet"])
def test_missing_or_invalid_collet_does_not_fall_back_to_another_binding(publication, collet):
    store, _, binding, service = publication
    error = ContextCollabNotFoundError if collet.strip() else ContextCollabValidationError
    with pytest.raises(error):
        service.publish_opportunity(opportunity_command(collet_id=collet))
    assert store.list_opportunities(AXES, binding.method_binding_id, COLLET_ID) == []


def test_publish_propagates_storage_write_failure(publication):
    store, repo, binding, _ = publication
    failing = Mock(spec=MethodStoreProtocol, wraps=store)
    failing.insert_opportunity.side_effect = RuntimeError("write failed")
    service = OpenMethodService(failing, StructuralMethodOpsAuthorizer(), repo)
    with pytest.raises(RuntimeError, match="write failed"):
        service.publish_opportunity(opportunity_command())
    assert store.list_opportunities(AXES, binding.method_binding_id, COLLET_ID) == []


@pytest.mark.parametrize("removed", [
    "collab_context_id", "context", "scope_ref", "opens_at",
    "binding_id", "method_binding_id",
])
def test_publish_command_rejects_removed_parameters(removed):
    command = opportunity_command()
    kwargs = {field.name: getattr(command, field.name) for field in fields(command)}
    with pytest.raises(TypeError, match=removed):
        OpenOpportunityCommand(**kwargs, **{removed: "caller_override"})


def test_publish_opens_immediately_at_server_time_in_own_collet(publication):
    store, _, binding, service = publication
    before = datetime.now(timezone.utc)
    result = service.publish_opportunity(opportunity_command())
    after = datetime.now(timezone.utc)
    row = store.get_opportunity(AXES, result.object_id, binding.method_binding_id, COLLET_ID)
    opens_at = datetime.fromisoformat(row.opens_at)
    assert before <= opens_at <= after
    assert opens_at.utcoffset() == timedelta(0)
    assert row.transitioned_at == row.opens_at
    assert row.scope_ref == COLLET_ID
    assert service.query_opportunities_by_skill(actor_context=make_actor()) == (row,)


@pytest.mark.parametrize("transition", [None, "pause_opportunity", "close_opportunity"])
def test_publish_replay_keeps_first_timestamp_and_state_after_expiry(
    publication, monkeypatch, transition
):
    store, _, binding, service = publication
    clock = Mock(wraps=datetime)
    first_time = datetime(2030, 1, 1, tzinfo=timezone.utc)
    clock.now.return_value = first_time
    monkeypatch.setattr(open_base, "datetime", clock)
    monkeypatch.setattr(opportunity_ops, "datetime", clock)
    command = opportunity_command(closes_at=(first_time + timedelta(hours=1)).isoformat())
    first = service.publish_opportunity(command)
    if transition is not None:
        getattr(service, transition)(
            COLLET_ID, binding.method_binding_id, first.object_id,
            OpenOpportunityOpCommand(), make_actor(),
        )
    row = store.get_opportunity(AXES, first.object_id, binding.method_binding_id, COLLET_ID)
    clock.now.return_value = first_time + timedelta(hours=2)
    replay = service.publish_opportunity(command)
    assert replay.object_id == first.object_id
    assert replay.state == row.state.value
    assert row.opens_at == first_time.isoformat()
    assert store.get_opportunity(
        AXES, first.object_id, binding.method_binding_id, COLLET_ID
    ) is row
    assert store.list_opportunities(AXES, binding.method_binding_id, COLLET_ID) == [row]
    assert service.query_opportunities_by_skill(actor_context=make_actor()) == ()
