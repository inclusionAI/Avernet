"""Binding-free Open publication resolves within the caller's Collet/store axes."""

import inspect
from dataclasses import fields, replace
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
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
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
    names = {field.name for field in fields(OpenOpportunityCommand)}
    assert not names & {"binding_id", "method_binding_id"}
    for required in ("collab_context_id", "context"):
        kwargs = {name: getattr(command, name) for name in names - {required}}
        with pytest.raises(TypeError, match=required):
            OpenOpportunityCommand(**kwargs)


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
    assert row.transitioned_by == command.context.actor.principal_id
    assert row.transition_reason == command.reason
    # Participation scope is deliberately not the binding's parent scope.
    assert row.scope_ref == command.scope_ref != binding.parent_scope_ref
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
            service.publish_opportunity(replace(command, context=make_actor("stranger")))
        rows = store.list_opportunities(AXES, binding.method_binding_id, COLLET_ID)
        assert len(rows) == int(published)


@pytest.mark.parametrize("collet", ["", " ", "unknown_collet"])
def test_missing_or_invalid_collet_does_not_fall_back_to_another_binding(publication, collet):
    store, _, binding, service = publication
    error = ContextCollabNotFoundError if collet.strip() else ContextCollabValidationError
    with pytest.raises(error):
        service.publish_opportunity(opportunity_command(collab_context_id=collet))
    assert store.list_opportunities(AXES, binding.method_binding_id, COLLET_ID) == []


def test_publish_propagates_storage_write_failure(publication):
    store, repo, binding, _ = publication
    failing = Mock(spec=MethodStoreProtocol, wraps=store)
    failing.insert_opportunity.side_effect = RuntimeError("write failed")
    service = OpenMethodService(failing, StructuralMethodOpsAuthorizer(), repo)
    with pytest.raises(RuntimeError, match="write failed"):
        service.publish_opportunity(opportunity_command())
    assert store.list_opportunities(AXES, binding.method_binding_id, COLLET_ID) == []
