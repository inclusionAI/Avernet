"""Pure Open admission produces scoped identities, not Group/Single artifacts."""

from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    ScopedParticipantBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenAdmitCommand,
    OpenDecisionOpCommand,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
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


def _pure_open():
    store, _, lifecycle, service = make_open_service()
    binding = active_open_binding(lifecycle)
    service.publish_opportunity(
        opportunity_command(
            scope_ref=COLLET_ID, participation_type=ParticipationType.COLLET_PARTICIPANT
        ),
    )
    return store, service, binding


def _screen(service, binding, **application_overrides):
    submitted_application(service, binding_id=binding, **application_overrides)
    app = application_overrides.get("application_id", "app_1")
    service.screen(COLLET_ID, binding, app, OpenDecisionOpCommand(), make_actor())
    return app


def test_collet_participant_query_admit_replay_and_revoke():
    store, service, binding = _pure_open()
    assert len(service.query_opportunities(COLLET_ID, binding, make_actor())) == 1
    assert store.list_participant_bindings(AXES, binding, COLLET_ID) == []
    app = submitted_application(service, binding_id=binding)
    with pytest.raises(ContextCollabValidationError, match="does not accept event"):
        service.admit(
            COLLET_ID, binding, app,
            OpenAdmitCommand(admitted_scope_ref=COLLET_ID), make_actor(),
        )
    assert store.list_participant_bindings(AXES, binding, COLLET_ID) == []
    service.screen(COLLET_ID, binding, app, OpenDecisionOpCommand(), make_actor())
    command = OpenAdmitCommand(admitted_scope_ref=COLLET_ID, decision_id="dec_pure")
    admitted = service.admit(COLLET_ID, binding, app, command, make_actor())
    assert admitted.state == "ADMITTED"
    [participant] = store.list_participant_bindings(AXES, binding, COLLET_ID)
    assert participant.scope_ref == COLLET_ID
    assert participant.participant_ref == "bot_worker_001"
    assert participant.role_binding_refs == ("role_binding_py",)
    assert ScopedParticipantBinding.from_dict(participant.to_dict()) == participant
    [decision] = store.list_decisions(AXES, binding, COLLET_ID)
    assert decision.application_ref == app
    assert decision.admitted_scope_ref == COLLET_ID
    assert decision.resulting_binding_refs == (participant.participant_binding_id,)
    service.admit(COLLET_ID, binding, app, command, make_actor())
    assert store.list_participant_bindings(AXES, binding, COLLET_ID) == [participant]
    service.revoke_admission(
        COLLET_ID, binding, app, OpenDecisionOpCommand(), make_actor()
    )
    assert store.get_application(AXES, app, binding, COLLET_ID).state is (
        ApplicationState.REVOKED
    )
    # Evidence survives; this record alone is never an active membership grant.
    assert store.list_participant_bindings(AXES, binding, COLLET_ID) == [participant]
    assert store.list_decisions(AXES, binding, COLLET_ID) == [decision]
    [method] = store.list_bindings(AXES, COLLET_ID)
    assert method.method_names == ("Open",)
    assert store.get_roster(AXES, binding, COLLET_ID) is None
    assert store.list_members(AXES, binding, COLLET_ID) == []
    assert store.list_selections(AXES, binding, COLLET_ID) == []
    assert store.list_composition_edges(AXES, COLLET_ID) == []


@pytest.mark.parametrize("scope", [None, "another_collet", "a_child_task"])
def test_admission_cannot_omit_or_expand_collet_scope(scope):
    store, service, binding = _pure_open()
    app = _screen(service, binding)
    with pytest.raises(ContextCollabValidationError, match="scope must match"):
        service.admit(
            COLLET_ID, binding, app,
            OpenAdmitCommand(admitted_scope_ref=scope), make_actor(),
        )
    assert store.list_participant_bindings(AXES, binding, COLLET_ID) == []
    assert store.list_decisions(AXES, binding, COLLET_ID) == []
    assert store.get_application(AXES, app, binding, COLLET_ID).state is (
        ApplicationState.SCREENING
    )


def test_participant_binding_store_isolation_and_idempotency():
    store = InMemoryMethodStore()
    participant = ScopedParticipantBinding("pb_1", COLLET_ID, "agent_1", ("role_1",))
    assert store.insert_participant_binding(AXES, "mb_1", participant, COLLET_ID) == (
        participant
    )
    assert store.insert_participant_binding(AXES, "mb_1", participant, COLLET_ID) == (
        participant
    )
    with pytest.raises(ContextCollabValidationError, match="stored shape"):
        store.insert_participant_binding(
            AXES, "mb_1", replace(participant, participant_ref="another"), COLLET_ID
        )
    for axes, method, context in (
        (StoreAxes("other_tenant", AXES.env), "mb_1", COLLET_ID),
        (StoreAxes(AXES.avernet_tenant, "other_env"), "mb_1", COLLET_ID),
        (AXES, "mb_2", COLLET_ID),
        (AXES, "mb_1", "another_collet"),
    ):
        assert store.get_participant_binding(axes, "pb_1", method, context) is None
        assert store.list_participant_bindings(axes, method, context) == []
    with pytest.raises(ContextCollabValidationError, match="scope must match"):
        store.insert_participant_binding(AXES, "mb_1", participant, "another_collet")


@pytest.mark.parametrize("change", [
    {"scope_ref": ""}, {"participant_ref": ""}, {"participant_binding_id": ""},
    {"role_binding_refs": [""]}, {"role_binding_refs": "role"}, {"unknown": True},
])
def test_participant_binding_rejects_invalid_fields(change):
    data = ScopedParticipantBinding("pb_1", COLLET_ID, "agent_1").to_dict()
    with pytest.raises(ContextCollabValidationError):
        ScopedParticipantBinding.from_dict({**data, **change})


def test_participant_write_failure_is_not_admission(monkeypatch):
    store, service, binding = _pure_open()
    app = _screen(service, binding)

    def fail(*args):
        raise RuntimeError("participant storage unavailable")

    monkeypatch.setattr(store, "insert_participant_binding", fail)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        service.admit(
            COLLET_ID, binding, app,
            OpenAdmitCommand(admitted_scope_ref=COLLET_ID), make_actor(),
        )
    assert store.list_decisions(AXES, binding, COLLET_ID) == []
    assert store.list_participant_bindings(AXES, binding, COLLET_ID) == []
    assert store.get_application(AXES, app, binding, COLLET_ID).state is (
        ApplicationState.SCREENING
    )


def test_admission_replay_finishes_failed_state_write_without_duplicate(monkeypatch):
    store, service, binding = _pure_open()
    app = _screen(service, binding)
    command = OpenAdmitCommand(admitted_scope_ref=COLLET_ID, decision_id="dec_retry")
    with monkeypatch.context() as patch:
        def fail(*args):
            raise RuntimeError("application storage unavailable")
        patch.setattr(store, "update_application_state", fail)
        with pytest.raises(RuntimeError, match="storage unavailable"):
            service.admit(COLLET_ID, binding, app, command, make_actor())
    assert store.get_application(AXES, app, binding, COLLET_ID).state is (
        ApplicationState.SCREENING
    )
    assert service.admit(COLLET_ID, binding, app, command, make_actor()).state == (
        "ADMITTED"
    )
    assert len(store.list_participant_bindings(AXES, binding, COLLET_ID)) == 1
    assert len(store.list_decisions(AXES, binding, COLLET_ID)) == 1


def test_decision_replay_cannot_admit_a_different_application():
    store, service, binding = _pure_open()
    app = _screen(service, binding)
    command = OpenAdmitCommand(admitted_scope_ref=COLLET_ID, decision_id="dec_same")
    service.admit(COLLET_ID, binding, app, command, make_actor())
    second = _screen(
        service, binding, application_id="app_second",
        applicant_agent_ref="agent_second",
    )
    with pytest.raises(ContextCollabValidationError, match="another application"):
        service.admit(COLLET_ID, binding, second, command, make_actor())
    assert store.get_application(AXES, second, binding, COLLET_ID).state is (
        ApplicationState.SCREENING
    )
    assert len(store.list_participant_bindings(AXES, binding, COLLET_ID)) == 1
