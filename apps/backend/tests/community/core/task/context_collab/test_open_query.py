"""L5 Open opportunity queries: authenticated, binding-scoped and read-only."""

from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
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


def _snapshot(store, binding):
    return (
        store.get_binding(AXES, binding, COLLET_ID),
        tuple(store.list_opportunities(AXES, binding, COLLET_ID)),
        tuple(store.list_applications(AXES, binding, COLLET_ID)),
        tuple(store.list_decisions(AXES, binding, COLLET_ID)),
        store.get_roster(AXES, binding, COLLET_ID),
        tuple(store.list_members(AXES, binding, COLLET_ID)),
    )


@pytest.fixture
def published():
    store, _, lifecycle, service = make_open_service()
    binding = active_open_binding(lifecycle)
    service.publish_opportunity(COLLET_ID, binding, opportunity_command(), make_actor())
    return store, binding, service


def test_query_is_a_real_open_protocol_operation_and_never_admits(published):
    store, binding, service = published
    assert isinstance(service, OpenMethodOpsServiceProtocol)
    before = _snapshot(store, binding)
    # A previously unadmitted authenticated caller may read; it is not the owner.
    stranger = make_actor("unadmitted_reader")
    [opportunity] = service.query_opportunities(COLLET_ID, binding, stranger)
    assert opportunity.opportunity_id == "opp_alpha"
    assert opportunity.method_binding_id == binding
    assert service.query_opportunities(COLLET_ID, binding, stranger) == (opportunity,)
    assert _snapshot(store, binding) == before


@pytest.mark.parametrize("state", list(OpportunityState))
def test_query_only_returns_open_opportunities(published, state):
    store, binding, service = published
    opportunity = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.update_opportunity_state(AXES, replace(opportunity, state=state), COLLET_ID)
    before = _snapshot(store, binding)
    result = service.query_opportunities(COLLET_ID, binding, make_actor())
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
    assert (
        bool(service.query_opportunities(COLLET_ID, binding, make_actor())) is visible
    )
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
    result = service.query_opportunities(COLLET_ID, binding, make_actor())
    occupied = state in {
        ApplicationState.SUBMITTED,
        ApplicationState.SCREENING,
        ApplicationState.ADMITTED,
    }
    assert bool(result) is not (capacity == 1 and occupied)
    assert _snapshot(store, binding) == before


def test_capacity_is_counted_per_opportunity_and_query_is_binding_scoped(published):
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
        COLLET_ID,
        binding,
        opportunity_command(opportunity_id="opp_free", capacity=1),
        make_actor(),
    )
    other_binding = replace(
        store.get_binding(AXES, binding, COLLET_ID), method_binding_id="mb_other"
    )
    store.upsert_binding(AXES, other_binding)
    service.publish_opportunity(
        COLLET_ID,
        other_binding.method_binding_id,
        opportunity_command(opportunity_id="opp_other_binding"),
        make_actor(),
    )
    [result] = service.query_opportunities(COLLET_ID, binding, make_actor())
    assert result.opportunity_id == "opp_free"
    with pytest.raises(ContextCollabNotFoundError):
        service.query_opportunities("other_context", binding, make_actor())


@pytest.mark.parametrize(
    "state", [BindingState.BOUND, BindingState.SUSPENDED, BindingState.TERMINATED]
)
def test_query_requires_an_active_binding(published, state):
    store, binding, service = published
    store.replace_binding(
        AXES, replace(store.get_binding(AXES, binding, COLLET_ID), state=state)
    )
    before = _snapshot(store, binding)
    with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
        service.query_opportunities(COLLET_ID, binding, make_actor())
    assert _snapshot(store, binding) == before


def test_query_rejects_non_open_missing_and_unauthenticated_targets(published):
    store, binding, service = published
    before = _snapshot(store, binding)
    with pytest.raises(ContextCollabNotFoundError):
        service.query_opportunities(COLLET_ID, "missing", make_actor())
    with pytest.raises(ContextCollabValidationError, match="not allowed"):
        service.query_opportunities(COLLET_ID, binding, make_actor(""))
    assert _snapshot(store, binding) == before
    store.replace_binding(
        AXES,
        replace(
            store.get_binding(AXES, binding, COLLET_ID),
            method_kind="Group",
            method_names=("Group",),
            profile_versions={"Group": "0.2"},
        ),
    )
    with pytest.raises(ContextCollabValidationError, match="declare Open"):
        service.query_opportunities(COLLET_ID, binding, make_actor())
