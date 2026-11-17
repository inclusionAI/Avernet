"""Creation receipts serialize Collet without leaking transport state into L4."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from agentclaw.community.adapters.http.task.schemas_context_collab import (
    created_collet_to_dto,
    status_to_dto,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.views import (
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.service_api import (
    CollaborationCreateCommand,
    ContextCollabStatusProjection,
    TaskProgressProjectionDTO,
)


def _initial_event(collet):
    return EventRecord(
        collab_context_id=collet.collet_id,
        event_id="evt_created",
        parent_event=None,
        event_kind="task.created",
        primitive_type="Task",
        actor_ref=collet.initiator_ref,
        visibility="public",
        payload={},
        context_version_after=collet.version,
        occurred_at=datetime(2026, 11, 17, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    ("method_kind", "canonical_method", "executor"),
    [
        ("Single", "Single", AgentRef("bot", "worker")),
        ("Open", "Open", None),
        ("Open + Group", "Open + Group", None),
    ],
)
def test_creation_receipt_keeps_wire_shape_and_inputs(
    method_kind, canonical_method, executor
):
    command = CollaborationCreateCommand(
        to_space=SpaceRef("space", "test"),
        principal_ref=PrincipalRef("human", "owner"),
        product_kind=None,
        workflow_kind=None,
        method_kind=method_kind,
        executor_ref=executor,
        objective="Review the change",
        resource_refs=[],
    )
    collet = Collet(
        collet_id="collet_created",
        initiator_ref=PrincipalRef("human", "creator"),
        state=ColletState.ACTIVE,
        scope=command.to_space,
        version=1,
    )
    original = deepcopy((collet, command))

    initial_event = _initial_event(collet)
    later_event = replace(
        initial_event,
        event_id="evt_later",
        parent_event=initial_event.event_id,
        event_kind="user_interaction.posted",
        primitive_type="Message",
        context_version_after=2,
    )
    result = created_collet_to_dto(collet, command, [initial_event, later_event])

    assert result.model_dump() == {
        "collab_context_id": "collet_created",
        "context_version": 1,
        "current_snapshot": {
            "objective": "Review the change",
            "status": "created",
            # Accountable principal is not necessarily the initiating actor.
            "principal_ref": "owner",
            "executor_ref": "worker" if executor else None,
            "method_kind": canonical_method,
            "progress": {
                "percent": 0,
                "completed_count": 0,
                "active_count": 0,
                "blocked_count": 0,
                "current_event_id": "evt_created",
            },
        },
        "current_event": {
            "event_id": "evt_created",
            "parent_event": None,
            "event_kind": "task.created",
            "primitive_type": "Task",
        },
    }
    assert (collet, command) == original


def test_creation_receipt_rejects_missing_initial_event():
    command = CollaborationCreateCommand(
        to_space=SpaceRef("space", "test"),
        principal_ref=PrincipalRef("human", "owner"),
        product_kind=None,
        workflow_kind=None,
        method_kind="Open",
        executor_ref=None,
        objective="Review the change",
        resource_refs=[],
    )
    collet = Collet(
        collet_id="collet_created",
        initiator_ref=command.principal_ref,
        state=ColletState.ACTIVE,
        scope=command.to_space,
        version=1,
    )

    with pytest.raises(ValueError, match="initial event"):
        created_collet_to_dto(collet, command, [])


@pytest.mark.parametrize("count", [0, 1, 2])
def test_methodless_status_preserves_all_executors_and_legacy_wire_shape(count):
    executors = tuple(AgentRef("bot", f"worker-{i}") for i in range(count))
    view = CurrentSnapshot(
        objective="Deliver work",
        status="created",
        principal_ref=PrincipalRef("human", "owner"),
        executor_ref=executors[0] if count == 1 else None,
        executor_refs=executors if count > 1 else (),
        method_kind=None,
        progress=ProgressSnapshot(),
    )
    response = status_to_dto(
        ContextCollabStatusProjection(
            collab_context_id="collet_created",
            context_version=1,
            current_snapshot=view.to_dict(),
            current_event=None,
            task_progress=TaskProgressProjectionDTO(total_events=0, events=[]),
        )
    )
    payload = response.model_dump()["current_snapshot"]
    assert payload["method_kind"] is None
    assert payload["executor_ref"] == ("worker-0" if count == 1 else None)
    if count > 1:
        assert payload["executor_refs"] == ["worker-0", "worker-1"]
    else:
        assert "executor_refs" not in payload
    # The compatibility serializer also respects normal Pydantic exclusions.
    filtered = response.current_snapshot.model_dump(exclude={"executor_refs"})
    assert "executor_refs" not in filtered
