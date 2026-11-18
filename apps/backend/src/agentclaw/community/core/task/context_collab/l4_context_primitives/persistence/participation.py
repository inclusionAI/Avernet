"""Atomic initial L4 participant/resource binding on an already locked Collet.

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
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    COLLET_BINDING_EVENT,
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
    task: Task | None,
    facts: ParticipationFacts,
    executor_refs: tuple[AgentRef, ...],
    actor: PrincipalRef,
    *,
    binding_event_id: str | None = None,
    resource_refs: tuple[ResourceRef, ...] = (),
) -> None:
    if task is not None and (task.executor_refs or task.version != 1):
        raise ContextCollabValidationError(
            "initial binding requires untouched unassigned Work"
        )
    if any(
        ref not in {p.agent_ref for p in facts.participants} for ref in executor_refs
    ):
        raise ContextCollabValidationError(
            "Task executor must be an actual Participant"
        )
    previous_version = int(row.context_version)
    now, event_id = datetime.now(timezone.utc), binding_event_id or "evt_" + uuid4().hex
    event = EventRecord(
        collab_context_id=row.collab_context_id,
        event_id=event_id,
        parent_event=task.last_event_ref if task is not None else None,
        event_kind=INITIAL_BINDING_EVENT if task is not None else COLLET_BINDING_EVENT,
        primitive_type="Task" if task is not None else "Collet",
        actor_ref=actor,
        visibility="workspace",
        payload={
            "participation": facts.to_dict(),
            "executor_refs": [r.to_dict() for r in executor_refs],
            "resource_refs": [ref.to_dict() for ref in resource_refs],
        },
        context_version_after=previous_version + 1,
        occurred_at=now,
        task_ref=task.task_id if task is not None else None,
        task_version_after=2 if task is not None else None,
    )
    if task is not None:
        updated_task = replace(
            task, executor_refs=executor_refs, version=2, last_event_ref=event_id
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
            ContextCollabColletModel.context_version == previous_version,
        )
        .update(
            {
                "context_version": previous_version + 1,
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
