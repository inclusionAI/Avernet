"""L5 Open discovery: authenticated, tenant/environment-scoped and read-only."""

import inspect
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    OpportunityState,
    ParticipationApplication,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenMethodOpsServiceProtocol,
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
    _iso_offset,
    active_open_binding,
    application_dict,
    make_actor,
    make_open_service,
    opportunity_command,
)


def _snapshot(store, binding, collab_context_id=COLLET_ID):
    return (
        store.get_binding(AXES, binding, collab_context_id),
        tuple(store.list_opportunities(AXES, binding, collab_context_id)),
        tuple(store.list_applications(AXES, binding, collab_context_id)),
        tuple(store.list_decisions(AXES, binding, collab_context_id)),
        store.get_roster(AXES, binding, collab_context_id),
        tuple(store.list_members(AXES, binding, collab_context_id)),
    )


@pytest.fixture
def published():
    store, _, lifecycle, service = make_open_service()
    binding = active_open_binding(lifecycle)
    service.publish_opportunity(opportunity_command())
    return store, binding, service


def test_query_is_a_real_open_protocol_operation_and_never_admits(published):
    store, binding, service = published
    assert isinstance(service, OpenMethodOpsServiceProtocol)
    before = _snapshot(store, binding)
    # A previously unadmitted authenticated caller may read; it is not the owner.
    stranger = make_actor("unadmitted_reader")
    [opportunity] = service.query_opportunities(actor_context=stranger)
    assert opportunity.opportunity_id == "opp_alpha"
    assert opportunity.method_binding_id == binding
    assert service.query_opportunities(actor_context=stranger) == (opportunity,)
    assert _snapshot(store, binding) == before


@pytest.mark.parametrize("state", list(OpportunityState))
def test_query_only_returns_open_opportunities(published, state):
    store, binding, service = published
    opportunity = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.update_opportunity_state(AXES, replace(opportunity, state=state), COLLET_ID)
    before = _snapshot(store, binding)
    result = service.query_opportunities_by_skill(actor_context=make_actor())
    assert bool(result) is (state is OpportunityState.OPEN)
    assert _snapshot(store, binding) == before


@pytest.mark.parametrize(
    "opens_hours,closes_hours,visible",
    [(-2, None, True), (-2, 1, True), (1, 2, False), (-2, -1, False)],
)
def test_query_obeys_the_application_window(
    published, opens_hours, closes_hours, visible
):
    store, binding, service = published
    opportunity = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.update_opportunity_state(
        AXES,
        replace(
            opportunity,
            opens_at=_iso_offset(opens_hours),
            closes_at=None if closes_hours is None else _iso_offset(closes_hours),
        ),
        COLLET_ID,
    )
    before = _snapshot(store, binding)
    assert bool(service.query_opportunities_by_skill(actor_context=make_actor())) is visible
    assert _snapshot(store, binding) == before


@pytest.mark.parametrize("state", list(ApplicationState))
@pytest.mark.parametrize("capacity", [None, 1, 2])
def test_query_uses_the_same_capacity_states_as_application(published, state, capacity):
    store, binding, service = published
    opportunity = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.update_opportunity_state(
        AXES, replace(opportunity, capacity=capacity), COLLET_ID
    )
    store.insert_application(
        AXES,
        binding,
        ParticipationApplication.from_dict(
            application_dict(opportunity_ref="opp_alpha", state=state.value)
        ),
        COLLET_ID,
    )
    before = _snapshot(store, binding)
    result = service.query_opportunities_by_skill(actor_context=make_actor())
    occupied = state in {
        ApplicationState.SUBMITTED,
        ApplicationState.SCREENING,
        ApplicationState.ADMITTED,
    }
    assert bool(result) is not (capacity == 1 and occupied)
    assert _snapshot(store, binding) == before


def test_capacity_is_isolated_per_opportunity_and_binding(published):
    store, binding, service = published
    opportunity = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.update_opportunity_state(AXES, replace(opportunity, capacity=1), COLLET_ID)
    store.insert_application(
        AXES,
        binding,
        ParticipationApplication.from_dict(
            application_dict(opportunity_ref="opp_alpha")
        ),
        COLLET_ID,
    )
    service.publish_opportunity(
        opportunity_command(opportunity_id="opp_free", capacity=1)
    )
    other_binding = replace(
        store.get_binding(AXES, binding, COLLET_ID), method_binding_id="mb_other"
    )
    store.upsert_binding(AXES, other_binding)
    # Publish while B is the only ACTIVE Open binding, then restore A to
    # verify query/capacity isolation with both bindings ACTIVE.
    original_binding = store.get_binding(AXES, binding, COLLET_ID)
    store.replace_binding(AXES, replace(original_binding, state=BindingState.SUSPENDED))
    service.publish_opportunity(opportunity_command(capacity=1))
    store.replace_binding(AXES, original_binding)
    results = service.query_opportunities_by_skill(actor_context=make_actor())
    assert [(item.method_binding_id, item.opportunity_id) for item in results] == [
        (binding, "opp_free"),
        ("mb_other", "opp_alpha"),
    ]


@pytest.mark.parametrize(
    "state", [BindingState.BOUND, BindingState.SUSPENDED, BindingState.TERMINATED]
)
def test_query_omits_inactive_bindings(published, state):
    store, binding, service = published
    store.replace_binding(
        AXES, replace(store.get_binding(AXES, binding, COLLET_ID), state=state)
    )
    before = _snapshot(store, binding)
    assert service.query_opportunities_by_skill(actor_context=make_actor()) == ()
    assert _snapshot(store, binding) == before


def test_query_omits_non_open_bindings(published):
    store, binding, service = published
    store.replace_binding(
        AXES,
        replace(
            store.get_binding(AXES, binding, COLLET_ID),
            method_kind="Group",
            method_names=("Group",),
            profile_versions={"Group": "0.2"},
        ),
    )
    before = _snapshot(store, binding)
    assert service.query_opportunities_by_skill(actor_context=make_actor()) == ()
    assert _snapshot(store, binding) == before


def test_query_accepts_only_actor_context(published):
    _, binding, service = published
    for method in (
        OpenMethodOpsServiceProtocol.query_opportunities,
        OpenMethodService.query_opportunities,
    ):
        assert list(inspect.signature(method).parameters) == ["self", "actor_context"]
    assert service.query_opportunities_by_skill(make_actor()) == service.query_opportunities_by_skill(
        actor_context=make_actor()
    )
    with pytest.raises(TypeError):
        service.query_opportunities_by_skill(COLLET_ID, binding, make_actor())
    with pytest.raises(TypeError):
        service.query_opportunities_by_skill(context=make_actor())


def test_query_without_bindings_returns_an_empty_tuple():
    store, _, _, service = make_open_service()
    assert service.query_opportunities_by_skill(actor_context=make_actor()) == ()
    assert store.list_bindings_in_axes(AXES) == []


@pytest.mark.parametrize("published_binding", [False, True])
def test_query_rejects_unauthenticated_actors_before_enumeration(published_binding):
    store, repo, lifecycle, _ = make_open_service()
    if published_binding:
        active_open_binding(lifecycle)
    observed_store = Mock(spec=MethodStoreProtocol, wraps=store)
    service = OpenMethodService(observed_store, StructuralMethodOpsAuthorizer(), repo)
    with pytest.raises(ContextCollabValidationError, match="not allowed"):
        service.query_opportunities(actor_context=make_actor(""))
    observed_store.list_bindings_in_axes.assert_not_called()


def test_query_discovers_multiple_collets_without_mixing_capacity(published):
    store, binding, service = published
    first = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.update_opportunity_state(AXES, replace(first, capacity=1), COLLET_ID)
    store.insert_application(
        AXES,
        binding,
        ParticipationApplication.from_dict(
            application_dict(opportunity_ref="opp_alpha")
        ),
        COLLET_ID,
    )
    other_collet = "collet_other"
    # Both IDs may be reused in another Collet; capacity must not leak across it.
    store.upsert_binding(
        AXES,
        replace(
            store.get_binding(AXES, binding, COLLET_ID), collab_context_id=other_collet
        ),
    )
    other = replace(first, scope_ref=other_collet, capacity=1)
    store.insert_opportunity(AXES, other, other_collet)
    before = (_snapshot(store, binding), _snapshot(store, binding, other_collet))
    assert service.query_opportunities_by_skill(actor_context=make_actor()) == (other,)
    assert (
        _snapshot(store, binding),
        _snapshot(store, binding, other_collet),
    ) == before


@pytest.mark.parametrize(
    "foreign_axes",
    [
        StoreAxes("another_tenant", AXES.env),
        StoreAxes(AXES.avernet_tenant, "another_env"),
    ],
)
def test_query_does_not_expose_other_tenants_or_environments(published, foreign_axes):
    store, binding, service = published
    local = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.update_opportunity_state(AXES, replace(local, capacity=1), COLLET_ID)
    store.upsert_binding(foreign_axes, store.get_binding(AXES, binding, COLLET_ID))
    store.insert_opportunity(foreign_axes, local, COLLET_ID)
    store.insert_application(
        foreign_axes,
        binding,
        ParticipationApplication.from_dict(
            application_dict(opportunity_ref="opp_alpha")
        ),
        COLLET_ID,
    )
    store.insert_opportunity(
        foreign_axes, replace(local, opportunity_id="opp_foreign"), COLLET_ID
    )
    [result] = service.query_opportunities_by_skill(actor_context=make_actor())
    assert result.opportunity_id == "opp_alpha"
    assert result.capacity == 1


def test_query_keeps_per_binding_g1_policy_and_omits_denied_candidates(published):
    store, binding, _ = published
    _, repo, _, _ = make_open_service()
    other_binding = replace(
        store.get_binding(AXES, binding, COLLET_ID), method_binding_id="mb_denied"
    )
    store.upsert_binding(AXES, other_binding)
    original = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.insert_opportunity(
        AXES, replace(original, method_binding_id="mb_denied"), COLLET_ID
    )
    authorizer = Mock(
        spec=MethodOpsAuthorizerProtocol, wraps=StructuralMethodOpsAuthorizer()
    )

    def check_candidate(op, candidate, actor_context, **identities):
        if candidate.method_binding_id == "mb_denied":
            raise ContextCollabValidationError("candidate is not allowed")
        StructuralMethodOpsAuthorizer().check_open_op(
            op, candidate, actor_context, **identities
        )

    authorizer.check_open_op.side_effect = check_candidate
    service = OpenMethodService(store, authorizer, repo)
    actor_context = make_actor("reader")
    assert service.query_opportunities(actor_context=actor_context) == (original,)
    authorizer.check_open_discovery.assert_called_once_with(actor_context)
    assert authorizer.check_open_op.call_count == 2


def test_query_propagates_storage_validation_errors(published):
    store, _, _ = published
    repo = Mock()
    repo.get_current_status.side_effect = ContextCollabValidationError("invalid record")
    service = OpenMethodService(store, StructuralMethodOpsAuthorizer(), repo)
    with pytest.raises(ContextCollabValidationError, match="invalid record"):
        service.query_opportunities(actor_context=make_actor())
