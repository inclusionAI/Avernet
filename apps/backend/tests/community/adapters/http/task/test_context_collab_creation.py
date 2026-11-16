"""Creation receipts serialize Collet without leaking transport state into L4."""

from copy import deepcopy

import pytest

from agentclaw.community.adapters.http.task.schemas_context_collab import (
    created_collet_to_dto,
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
from agentclaw.community.core.task.context_collab.service_api import ColletCreateCommand


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
    command = ColletCreateCommand(
        space=SpaceRef("space", "test"),
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
        scope=command.space,
        current_event_id="evt_created",
        version=1,
    )
    original = deepcopy((collet, command))

    result = created_collet_to_dto(collet, command)

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
    command = ColletCreateCommand(
        space=SpaceRef("space", "test"),
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
        scope=command.space,
        current_event_id=None,
        version=1,
    )

    with pytest.raises(ValueError, match="initial event"):
        created_collet_to_dto(collet, command)
