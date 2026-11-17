"""Atomic initial L4 bind-role/Task assignment on an already locked Collet.

The caller owns the SQL transaction. This operation knows no Product/Profile or
Method state; an upper-layer caller must authorize and validate eligibility first.
"""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    INITIAL_BINDING_EVENT,
    ParticipationFacts,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabColletModel,
    ContextCollabTaskModel,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.row_codec import (
    _collet_from_row,
    _dump_json,
    _event_row,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.utils.env_utils import get_current_env


def bind_initial_participants(
    db,
    row,
    task: Task,
    facts: ParticipationFacts,
    executor_refs: tuple[AgentRef, ...],
    actor: PrincipalRef,
) -> None:
    if task.executor_refs or task.version != 1 or int(row.context_version) != 1:
        raise ContextCollabValidationError(
            "initial binding requires untouched unassigned Work"
        )
    if any(
        ref not in {p.agent_ref for p in facts.participants} for ref in executor_refs
    ):
        raise ContextCollabValidationError(
            "Task executor must be an actual Participant"
        )
    now, event_id = datetime.now(timezone.utc), "evt_" + uuid4().hex
    updated_task = replace(
        task, executor_refs=executor_refs, version=2, last_event_ref=event_id
    )
    event = EventRecord(
        collab_context_id=task.collet_ref,
        event_id=event_id,
        parent_event=row.current_event_id,
        event_kind=INITIAL_BINDING_EVENT,
        primitive_type="Task",
        actor_ref=actor,
        visibility="workspace",
        payload={
            "participation": facts.to_dict(),
            "executor_refs": [r.to_dict() for r in executor_refs],
        },
        context_version_after=2,
        occurred_at=now,
        task_ref=task.task_id,
        task_version_after=2,
    )
    count = (
        db.query(ContextCollabTaskModel)
        .filter(
            ContextCollabTaskModel.task_id == task.task_id,
            ContextCollabTaskModel.collab_context_id == task.collet_ref,
            ContextCollabTaskModel.env == get_current_env(),
            ContextCollabTaskModel.task_version == 1,
        )
        .update(
            {
                "task_data": _dump_json(updated_task.to_dict()),
                "task_version": 2,
                "gmt_modified": now,
            },
            synchronize_session=False,
        )
    )
    if count != 1:
        raise ContextCollabVersionConflict("initial binding Task CAS failed")
    collet = _collet_from_row(row)
    metadata = (
        replace(collet.metadata, updated_at=now, updated_by=actor)
        if collet.metadata
        else None
    )
    count = (
        db.query(ContextCollabColletModel)
        .filter(
            ContextCollabColletModel.id == row.id,
            ContextCollabColletModel.context_version == 1,
        )
        .update(
            {
                "current_event_id": event_id,
                "context_version": 2,
                "collet_metadata": _dump_json(metadata.to_dict()) if metadata else None,
                "gmt_modified": now,
            },
            synchronize_session=False,
        )
    )
    if count != 1:
        raise ContextCollabVersionConflict("initial binding Collet CAS failed")
    db.add(_event_row(event, now=now))
    db.flush()
