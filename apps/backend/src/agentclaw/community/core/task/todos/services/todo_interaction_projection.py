"""Projection helpers for versioned Todo interaction streams."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from agentclaw.community.core.task.todos.artifact_models import TodoExecutionRecord
from agentclaw.community.core.task.todos.collaboration.protocol import (
    TodoCollaborationEvent,
)
from agentclaw.community.core.task.todos.models import (
    TodoArtifactRecord as TodoEventArtifactRecord,
)
from agentclaw.community.core.task.todos.models import (
    TodoEventRecord,
    TodoInteractionMention,
)

INTERACTION_EVENT_TYPES = frozenset(
    {
        "task.created",
        "user_interaction.posted",
        "task.completed",
        "task.rejected",
        "artifact.reported",
    }
)
REPLYABLE_INTERACTION_EVENT_TYPES = INTERACTION_EVENT_TYPES - {"task.completed"}


def interaction_payload(
    *,
    message: str,
    author_name: str | None,
    reply_to_event_id: str | None,
    mentions: tuple[TodoInteractionMention, ...],
    target_execution_id: str | None = None,
    target_stage_instance_id: str | None = None,
) -> dict[str, Any]:
    """Build the durable payload for one Human interaction event."""
    payload: dict[str, Any] = {"content": message}
    optional_values = {
        "author_name": author_name,
        "reply_to_event_id": reply_to_event_id,
        "target_execution_id": target_execution_id,
        "target_stage_instance_id": target_stage_instance_id,
    }
    payload.update({key: value for key, value in optional_values.items() if value})
    if mentions:
        payload["mentions"] = [
            {"actor_type": mention.actor_type.value, "actor_id": mention.actor_id}
            for mention in mentions
        ]
    return payload


def select_projection_executions(
    executions: Iterable[TodoExecutionRecord],
    *,
    stage_order: Mapping[str, int],
) -> tuple[TodoExecutionRecord, ...]:
    """Read each Context once, attributed to its newest execution generation."""
    newest_by_context: dict[str, TodoExecutionRecord] = {}
    for execution in executions:
        current = newest_by_context.get(execution.collab_context_id)
        if current is None or _execution_generation_key(
            execution
        ) > _execution_generation_key(current):
            newest_by_context[execution.collab_context_id] = execution
    return tuple(
        sorted(
            newest_by_context.values(),
            key=lambda item: (
                stage_order.get(item.stage_instance_id or "", len(stage_order)),
                item.generation,
                item.created_at,
                item.execution_id,
            ),
        )
    )


def project_event_records(
    events: Iterable[TodoCollaborationEvent],
    *,
    execution: TodoExecutionRecord | None = None,
    interaction_allowed: bool,
    is_latest_override: bool | None = None,
) -> tuple[TodoEventRecord, ...]:
    return tuple(
        project_event_record(
            event,
            execution=execution,
            interaction_allowed=interaction_allowed,
            is_latest_override=is_latest_override,
        )
        for event in events
    )


def project_event_record(
    event: TodoCollaborationEvent,
    *,
    execution: TodoExecutionRecord | None = None,
    interaction_allowed: bool,
    is_latest_override: bool | None = None,
) -> TodoEventRecord:
    is_latest = (
        is_latest_override
        if is_latest_override is not None
        else execution is None or execution.is_current
    )
    event_replyable = event.event_type in REPLYABLE_INTERACTION_EVENT_TYPES
    replyable = interaction_allowed and is_latest and event_replyable
    disabled_reason: str | None = None
    if not is_latest:
        disabled_reason = "superseded"
    elif not event_replyable:
        disabled_reason = "event_not_replyable"
    elif not interaction_allowed:
        disabled_reason = "todo_not_replyable"
    return TodoEventRecord(
        event_id=event.event_id,
        event_seq=event.event_seq,
        event_type=event.event_type,
        actor_id=event.actor_id,
        actor_type=event.actor_type,
        content=event.content,
        execution_id=event.execution_id,
        context_version=event.context_version,
        status=event.status,
        artifacts=tuple(
            TodoEventArtifactRecord(
                artifact_id=artifact.artifact_id or None,
                name=artifact.name,
                url=artifact.url,
            )
            for artifact in event.artifacts
        ),
        created_at=event.created_at,
        payload=dict(event.payload),
        todo_execution_id=execution.execution_id if execution else None,
        stage_instance_id=execution.stage_instance_id if execution else None,
        generation=execution.generation if execution else None,
        is_latest=is_latest,
        replyable=replyable,
        reply_disabled_reason=disabled_reason,
    )


def _execution_generation_key(
    execution: TodoExecutionRecord,
) -> tuple[int, bool, datetime, str]:
    return (
        execution.generation,
        execution.is_current,
        execution.created_at,
        execution.execution_id,
    )
