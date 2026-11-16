"""Construct L4 creation records without product selection or Method state."""

from datetime import datetime, timezone
from uuid import uuid4

from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    CreatedColletRecord,
    InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
    TaskStatus,
)

_CREATION_EVENT_VISIBILITY = "public"


def build_created_collet(
    command: ColletCreateCommand,
    initial_event_draft: InitialEventDraft,
    completion_conditions: tuple[str, ...] = (),
) -> CreatedColletRecord:
    now = datetime.now(timezone.utc)
    collab_context_id = "collet_" + uuid4().hex
    event_id = "evt_" + uuid4().hex
    task = Task(
        task_id="task_" + uuid4().hex,
        collet_ref=collab_context_id,
        objective=command.objective,
        status=TaskStatus.CREATED.value,
        version=1,
        principal_ref=command.initiator_ref,
        executor_refs=command.executor_refs,
        parent_task_ref=None,
        completion_conditions=completion_conditions,
        created_event_ref=event_id,
        last_event_ref=event_id,
    )
    initial_event = EventRecord(
        collab_context_id=collab_context_id,
        event_id=event_id,
        parent_event=None,
        event_kind=initial_event_draft.event_kind,
        primitive_type=initial_event_draft.primitive_type,
        actor_ref=initial_event_draft.actor_ref,
        visibility=_CREATION_EVENT_VISIBILITY,
        payload=dict(initial_event_draft.payload),
        context_version_after=1,
        task_ref=task.task_id,
        task_version_after=task.version,
        occurred_at=now,
    )
    collet = Collet(
        collet_id=collab_context_id,
        # 创建主体与初始事件的 actor_ref 同源(创建人)。
        initiator_ref=initial_event_draft.actor_ref,
        state=ColletState.ACTIVE,
        scope=command.to_space,
        current_event_id=initial_event.event_id,
        version=1,
    )
    return CreatedColletRecord(collet, task, initial_event)
