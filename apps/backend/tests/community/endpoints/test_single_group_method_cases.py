"""Registered Single/Group transitions with persisted MethodStore assertions."""

from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    GroupMemberCommand,
    GroupMethodOpsServiceProtocol,
    SingleSelectCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from tests.community.factories.context_collab_methods import (
    BASE,
    BINDING,
    HEADERS,
    OWNER,
    seed_method,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_MEMBER = OWNER.actor.principal_id
_SCOPE = "scope://single"
_AGENT = "participant://worker"


def _register(kind, operation, state, happy):
    params = {"binding_id": BINDING}
    body = {"reason": "HTTP method transition"}
    if kind == "single":
        if operation in {"select", "replace"}:
            body.update(agent_participant_ref=_AGENT, scope_ref=_SCOPE)
        elif operation == "delegate":
            body.update(
                method_kind="Group",
                child_binding_id="mb_delegated",
                parent_scope_ref="scope://delegated",
                edge_id="edge_delegate",
            )
    else:
        body["participant_ref"] = _MEMBER
        if operation == "replace":
            body = {"old_participant_ref": _MEMBER, "new_participant_ref": "member-new"}
        elif operation == "change-role":
            body.update(role_binding_refs=["role://reviewer"], expected_revision=1)

    def seed(world):
        collet_id = seed_method(world, kind.title())
        params["collab_context_id"] = collet_id
        service = world.get(GroupMethodOpsServiceProtocol)
        if kind == "single" and operation in {"replace", "release"}:
            service.select(
                collet_id,
                BINDING,
                SingleSelectCommand("participant://old", _SCOPE),
                OWNER,
            )
        if kind == "group" and operation != "invite":
            member = GroupMemberCommand(_MEMBER, member_binding_id="member-http")
            service.invite(collet_id, BINDING, member, OWNER)
            if operation != "members":
                service.add_member(collet_id, BINDING, member, OWNER)
            if operation == "resume":
                service.suspend_member(collet_id, BINDING, member, OWNER)
            if operation == "change-role":
                body["expected_revision"] = (
                    world.get(MethodStoreProtocol)
                    .get_roster(current_axes(), BINDING, collet_id)
                    .revision
                )
        if not happy:
            params["binding_id"] = "unknown_binding"

    def check(response, world):
        store, axes = world.get(MethodStoreProtocol), current_axes()
        collet_id = params["collab_context_id"]
        if not happy:
            assert store.get_binding(axes, "unknown_binding", collet_id) is None
            if kind == "single":
                selections = store.list_selections(axes, BINDING, collet_id)
                assert len(selections) == (
                    1 if operation in {"replace", "release"} else 0
                )
                assert all(s.state.value == "ACTIVE" for s in selections)
            else:
                members = store.list_members(axes, BINDING, collet_id)
                assert len(members) == (0 if operation == "invite" else 1)
                if members:
                    assert members[0].state.value == (
                        "INVITED"
                        if operation == "members"
                        else "SUSPENDED"
                        if operation == "resume"
                        else "ACTIVE"
                    )
            return
        data = response.json()["data"]
        if kind == "single" and operation == "delegate":
            [edge] = store.list_composition_edges(axes, collet_id)
            assert edge.edge_id == data["edge"]["edge_id"] == "edge_delegate"
            assert edge.child_binding == "mb_delegated"
            assert (
                store.get_binding(axes, edge.child_binding, collet_id).state.value
                == "BOUND"
            )
            return
        assert data["accepted"] and data["state"] == state
        if kind == "single":
            selection = store.get_selection(axes, data["object_id"], BINDING, collet_id)
            assert selection.state.value == state
            if operation != "release":
                assert selection.selected_agent_participant_ref == _AGENT
            if operation == "replace":
                assert {
                    s.state.value
                    for s in store.list_selections(axes, BINDING, collet_id)
                } == {"ACTIVE", "REPLACED"}
        else:
            members = store.list_members(axes, BINDING, collet_id)
            member = next(
                m for m in members if m.group_member_binding_id == data["object_id"]
            )
            assert member.state.value == state
            if operation == "change-role":
                assert member.role_binding_refs == ("role://reviewer",)
            if operation == "replace":
                assert member.participant_ref == "member-new"
                assert {m.state.value for m in members} == {"ACTIVE", "REPLACED"}

    endpoint_test(
        method="POST",
        path=BASE + "/{binding_id}/" + kind + "/" + operation,
        scenario="method_transition_happy"
        if happy
        else "method_transition_missing_binding",
        input=CaseInput(path_params=params, headers=HEADERS, json_body=body),
        seed=seed,
        expect=ExpectSuccess(json_contains={"code": 200000})
        if happy
        else ExpectError(status=404, json_contains={"code": 404000}),
        extra_assertions=(check,),
    )(lambda: None)


for _kind, _operations in (
    (
        "single",
        (
            ("select", "ACTIVE"),
            ("replace", "ACTIVE"),
            ("release", "RELEASED"),
            ("delegate", "ACTIVE"),
        ),
    ),
    (
        "group",
        (
            ("invite", "INVITED"),
            ("members", "ACTIVE"),
            ("suspend", "SUSPENDED"),
            ("resume", "ACTIVE"),
            ("leave", "LEFT"),
            ("remove", "REMOVED"),
            ("replace", "ACTIVE"),
            ("change-role", "ACTIVE"),
        ),
    ),
):
    for _operation, _state in _operations:
        for _happy in (True, False):
            _register(_kind, _operation, _state, _happy)
