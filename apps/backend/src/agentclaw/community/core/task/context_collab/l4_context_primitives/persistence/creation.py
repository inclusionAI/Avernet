"""Construct independent L4 context and Work records; no L5/L6 selection."""

from datetime import datetime, timezone
from uuid import uuid4

from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet_metadata import (
    ColletMetadata,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    CreatedColletRecord,
    CreatedWorkRecord,
    InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
    TaskStatus,
)


def build_created_collet(
    command: ColletCreateCommand, actor_ref: PrincipalRef
) -> CreatedColletRecord:
    now = datetime.now(timezone.utc)
    return CreatedColletRecord(
        Collet(
            collet_id="collet_" + uuid4().hex,
            initiator_ref=actor_ref,
            state=ColletState.ACTIVE,
            scope=None,
            version=1,
            resources=command.resources,
            metadata=ColletMetadata(
                created_at=now,
                created_by=actor_ref,
                updated_at=now,
                updated_by=actor_ref,
                title=command.title,
                description=command.description,
                objective=command.objective,
                acceptance_criteria=command.acceptance_criteria,
            ),
        )
    )


def build_created_work(
    collet: Collet,
    principal_ref: PrincipalRef,
    objective: str,
    initial_event_draft: InitialEventDraft,
    completion_conditions: tuple[str, ...] = (),
    *,
    occurred_at: datetime,
) -> CreatedWorkRecord:
    event_id = "evt_" + uuid4().hex
    task = Task(
        task_id="task_" + uuid4().hex,
        collet_ref=collet.collet_id,
        objective=objective,
        status=TaskStatus.CREATED.value,
        version=1,
        principal_ref=principal_ref,
        executor_refs=(),
        parent_task_ref=None,
        completion_conditions=completion_conditions,
        created_event_ref=event_id,
        last_event_ref=event_id,
    )
    event = EventRecord(
        collab_context_id=collet.collet_id,
        event_id=event_id,
        parent_event=None,
        event_kind=initial_event_draft.event_kind,
        primitive_type=initial_event_draft.primitive_type,
        actor_ref=initial_event_draft.actor_ref,
        visibility="public",
        payload=dict(initial_event_draft.payload),
        context_version_after=collet.version,
        task_ref=task.task_id,
        task_version_after=task.version,
        occurred_at=occurred_at,
    )
    return CreatedWorkRecord(collet, task, event)
