"""HTTP request and response schemas for the Todo workbench."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactKind,
    TodoArtifactRecord,
)
from agentclaw.community.core.task.todos.stage_models import TodoStageRecord
from agentclaw.community.core.task.todos.models import (
    MAX_ACCEPTANCE_ID_LENGTH,
    MAX_ACCEPTANCE_TARGETS,
    MAX_ARTIFACT_ID_LENGTH,
    MAX_BOT_ID_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_EVENT_ID_LENGTH,
    MAX_MESSAGE_LENGTH,
    MAX_TITLE_LENGTH,
    TodoEventRecord,
    TodoRecord,
)


class TodoStatusSchema(_DocumentedEnum):
    """Six product states owned by A while runtime details stay in B."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    INTERRUPTED = "interrupted"
    PENDING_ACTION = "pending_action"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    __descriptions__ = {
        "not_started": "Created but no stage execution has started.",
        "in_progress": "A Bot stage is actively executing or the Todo is moving between Bot stages.",
        "interrupted": "Execution stopped after runtime recovery was exhausted and can be resumed.",
        "pending_action": "A Human action is planned or a stage is blocked awaiting action.",
        "completed": "All configured stages completed their required gates.",
        "cancelled": "The Todo was explicitly cancelled by a Human.",
    }


class TodoArtifactKindSchema(_DocumentedEnum):
    """Kind-specific rendering contract for a persisted Todo artifact."""

    TEXT = "text"
    FILE = "file"
    URL_LINK = "url_link"
    CODE = "code"

    __descriptions__ = {
        "text": "Inline textual output produced by the Bot.",
        "file": "Object-backed file available through the content endpoint.",
        "url_link": "A named external or internal URL produced by the Bot.",
        "code": "Source code or a code-oriented result produced by the Bot.",
    }


class CreateTodoRequest(BaseModel):
    """Create one pending Todo in the selected product Space."""

    space_id: int = Field(
        gt=0,
        description="Product Space that scopes the Todo and selects its Bots.",
    )
    title: str = Field(
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        description="Short Todo title shown in the workbench list.",
    )
    description: str = Field(
        max_length=MAX_DESCRIPTION_LENGTH,
        description="Todo objective persisted by A and passed to B when automatically dispatched.",
    )
    bot_id: str = Field(
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="Identifier of the Space-eligible Bot that will execute the Todo.",
    )


class TodoParticipantBindingRequest(BaseModel):
    """Concrete identity supplied for one template participant slot."""

    subject_type: Literal["bot", "human"] = Field(
        description="Identity kind required by the template slot."
    )
    subject_id: str = Field(
        min_length=1,
        max_length=256,
        description="Bot or Human identity used for this Todo instance.",
    )
    subject_name: str | None = Field(
        default=None,
        max_length=256,
        description="Optional display name; Bot names are resolved server-side.",
    )


class CreateTodoFromTemplateRequest(BaseModel):
    """Create a Todo from one published template version."""

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    description: str = Field(max_length=MAX_DESCRIPTION_LENGTH)
    template_id: str = Field(min_length=1, max_length=64)
    template_version_id: str = Field(min_length=1, max_length=64)
    participant_bindings: dict[str, TodoParticipantBindingRequest] = Field(
        description="Values for every participant slot required by the template."
    )


class CompleteTodoStageRequest(BaseModel):
    """Complete the current Human stage with an auditable result."""

    summary: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    idempotency_key: str = Field(min_length=1, max_length=256)
    result: dict[str, object] = Field(default_factory=dict)


class CancelTodoRequest(BaseModel):
    """Explicit Human cancellation request."""

    reason: str | None = Field(default=None, max_length=MAX_MESSAGE_LENGTH)


class TodoStageStatusSchema(_DocumentedEnum):
    """Public state of one expanded Todo stage."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"

    __descriptions__ = {
        "not_started": "The stage has not started.",
        "in_progress": "The stage is currently executing.",
        "blocked": "The stage cannot proceed without an action.",
        "completed": "The stage completed its configured outcome.",
    }


class TodoStagePendingActionSchema(_DocumentedEnum):
    """Reason a stage is waiting for a product action."""

    PLANNED_HUMAN_STAGE = "planned_human_stage"
    BLOCKED = "blocked"


class TodoStageResponse(BaseModel):
    """Expanded stage timeline item returned for a Todo."""

    stage_instance_id: str
    todo_id: str
    template_version_id: str
    stage_key: str
    stage_order: int
    stage_name: str
    purpose: str
    stage_kind: str
    executor_binding_key: str | None
    executor_subject_type: str
    executor_subject_id: str
    executor_subject_name: str
    owner_binding_key: str | None
    owner_user_id: str
    owner_user_name: str
    completion_gate: dict[str, object]
    status: TodoStageStatusSchema
    collab_context_id: str | None
    pending_action_kind: TodoStagePendingActionSchema | None
    state_reason: str | None
    requested_action: str | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_record(cls, record: TodoStageRecord) -> "TodoStageResponse":
        import json

        try:
            gate = json.loads(record.completion_gate_json)
        except (TypeError, json.JSONDecodeError):
            gate = {}
        return cls(
            stage_instance_id=record.stage_instance_id,
            todo_id=record.todo_id,
            template_version_id=record.template_version_id,
            stage_key=record.stage_key,
            stage_order=record.stage_order,
            stage_name=record.stage_name,
            purpose=record.purpose,
            stage_kind=record.stage_kind,
            executor_binding_key=record.executor_binding_key,
            executor_subject_type=record.executor_subject_type,
            executor_subject_id=record.executor_subject_id,
            executor_subject_name=record.executor_subject_name,
            owner_binding_key=record.owner_binding_key,
            owner_user_id=record.owner_user_id,
            owner_user_name=record.owner_user_name,
            completion_gate=gate if isinstance(gate, dict) else {},
            status=TodoStageStatusSchema(record.status.value),
            collab_context_id=record.collab_context_id,
            pending_action_kind=(
                TodoStagePendingActionSchema(record.pending_action_kind.value)
                if record.pending_action_kind is not None
                else None
            ),
            state_reason=record.state_reason,
            requested_action=record.requested_action,
            started_at=record.started_at.isoformat() if record.started_at else None,
            completed_at=record.completed_at.isoformat() if record.completed_at else None,
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
        )


class CreateTodoInteractionRequest(BaseModel):
    """Append one Human interaction event in the B collaboration Context."""

    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_LENGTH,
        description="Feedback or supplementary instruction for the pending execution.",
    )


class TodoAcceptanceTargetRequest(BaseModel):
    """Stable event or artifact selected as a rejection problem point."""

    kind: Literal["event", "artifact"] = Field(
        description="Whether the rejection target identifies an event or artifact."
    )
    id: str = Field(
        min_length=1,
        max_length=max(MAX_EVENT_ID_LENGTH, MAX_ARTIFACT_ID_LENGTH),
        description="Stable identifier of the selected event or artifact.",
    )
    floor: int = Field(gt=0, description="Presentation-only interaction floor.")


class SubmitTodoAcceptanceRequest(BaseModel):
    """Approve or reject the execution currently pending acceptance."""

    decision_id: str = Field(
        min_length=1,
        max_length=MAX_ACCEPTANCE_ID_LENGTH,
        description="Client-stable idempotency identifier for this Human decision.",
    )
    verdict: Literal["approved", "rejected"] = Field(
        description=(
            "Human decision for the pending execution: approve to complete it, "
            "or reject and return it to the original Bot session for rework."
        )
    )
    message: str | None = Field(
        default=None,
        max_length=MAX_MESSAGE_LENGTH,
        description="Full rejection instruction, or an optional approval note.",
    )
    targets: list[TodoAcceptanceTargetRequest] = Field(
        default_factory=list,
        max_length=MAX_ACCEPTANCE_TARGETS,
        description="Stable interaction event/artifact references selected on rejection.",
    )


class TodoArtifactResponse(BaseModel):
    """Executed result returned by B for one event."""

    artifact_id: str | None = Field(
        description="B-owned artifact identifier, if B provided one.",
    )
    name: str = Field(description="Display name of the produced artifact.")
    url: str = Field(
        description="Address from which the artifact can be opened or downloaded."
    )


class TodoArtifactRecordResponse(BaseModel):
    """One persisted Todo execution artifact."""

    artifact_id: str = Field(description="A-owned persisted artifact identifier.")
    todo_id: str = Field(description="Todo that owns this artifact.")
    todo_execution_id: str = Field(
        description="Todo execution generation that reported this artifact."
    )
    artifact_name: str = Field(description="Human-readable artifact display name.")
    kind: TodoArtifactKindSchema = Field(
        description="Rendering and validation kind of this artifact."
    )
    content: dict[str, object] = Field(
        description=(
            "Kind-specific public artifact content; internal object-storage IDs are omitted."
        )
    )
    created_at: str = Field(
        description="Artifact creation timestamp in ISO-8601 format."
    )

    @classmethod
    def from_record(cls, record: TodoArtifactRecord) -> "TodoArtifactRecordResponse":
        content = dict(record.content)
        if record.artifact_kind is TodoArtifactKind.FILE:
            content.pop("storage_id", None)
        return cls(
            artifact_id=record.artifact_id,
            todo_id=record.todo_id,
            todo_execution_id=record.execution_id,
            artifact_name=record.artifact_name,
            kind=TodoArtifactKindSchema(record.artifact_kind.value),
            content=content,
            created_at=record.created_at.isoformat(),
        )


class TodoEventResponse(BaseModel):
    """Normalized B collaboration event returned through A."""

    event_id: str = Field(description="B-owned immutable event identifier.")
    event_seq: int = Field(
        description="Monotonic sequence of this event inside the collaboration Context."
    )
    event_type: str = Field(
        description="B event type, such as task.created or user_interaction.posted."
    )
    actor_id: str = Field(
        description="Identifier of the human, Bot, or system that produced the event."
    )
    actor_type: str = Field(description="Actor kind, such as human, bot, or system.")
    content: str = Field(
        description="Primary text or status content carried by the event."
    )
    execution_id: str = Field(
        description="B execution identifier that owns this event."
    )
    context_version: int | None = Field(
        description="B Context version after this event, or null when unavailable."
    )
    status: str | None = Field(
        description="B execution status after this event, if reported."
    )
    artifacts: list[TodoArtifactResponse] = Field(
        description="Artifacts produced by the execution at this event."
    )
    created_at: str = Field(
        description="B event creation timestamp in ISO-8601 format."
    )
    @classmethod
    def from_record(cls, record: TodoEventRecord) -> "TodoEventResponse":
        return cls(
            event_id=record.event_id,
            event_seq=record.event_seq,
            event_type=record.event_type,
            actor_id=record.actor_id,
            actor_type=record.actor_type,
            content=record.content,
            execution_id=record.execution_id,
            context_version=record.context_version,
            status=record.status,
            artifacts=[
                TodoArtifactResponse(
                    artifact_id=item.artifact_id,
                    name=item.name,
                    url=item.url,
                )
                for item in record.artifacts
            ],
            created_at=record.created_at,
        )


class TodoInteractionResponse(TodoEventResponse):
    """Richer Human/Bot interaction event including its complete B payload."""

    payload: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "Complete B event payload retained for interaction content and "
            "rejection target presentation."
        ),
    )

    @classmethod
    def from_record(cls, record: TodoEventRecord) -> "TodoInteractionResponse":
        compact = TodoEventResponse.from_record(record)
        return cls(**compact.model_dump(), payload=dict(record.payload))


class TodoResponse(BaseModel):
    """Public Todo payload without collaboration-owner transport details."""

    todo_id: str = Field(description="A-owned Todo identifier.")
    space_id: int = Field(description="Product Space containing this Todo.")
    title: str = Field(description="Todo title shown in the workbench.")
    description: str = Field(
        description="Todo objective persisted by A and passed to B at dispatch time."
    )
    bot_id: str = Field(description="Bot selected to execute the Todo.")
    bot_name: str = Field(
        description="Snapshot of the Bot name taken when the Todo was created."
    )
    status: TodoStatusSchema = Field(
        description="A-owned persisted Todo product state."
    )
    collab_context_id: str | None = Field(
        description="B collaboration Context identifier, present only after successful dispatch."
    )
    created_at: str = Field(description="ISO-8601 creation timestamp of the Todo.")
    updated_at: str = Field(description="ISO-8601 last-update timestamp of the Todo.")
    template_id: str = Field(description="Template identity used to create this Todo.")
    template_version_id: str = Field(
        description="Immutable published template version used by this Todo."
    )
    template_name_snapshot: str = Field(
        description="Template name captured when this Todo was created."
    )
    current_stage_instance_id: str | None = Field(
        description="Current expanded stage instance, when stage execution exists."
    )
    pending_action_kind: str | None = Field(
        description="Why the Todo is waiting for an explicit Human action, if any."
    )
    state_reason: str | None = Field(description="Current product-state reason, if any.")
    requested_action: str | None = Field(description="Action requested from the Human, if any.")

    @classmethod
    def from_record(cls, record: TodoRecord) -> "TodoResponse":
        return cls(
            todo_id=record.todo_id,
            space_id=record.space_id,
            title=record.title,
            description=record.description,
            bot_id=record.bot_id,
            bot_name=record.bot_name,
            status=TodoStatusSchema(record.status.value),
            collab_context_id=record.collab_context_id,
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
            template_id=record.template_id,
            template_version_id=record.template_version_id,
            template_name_snapshot=record.template_name_snapshot,
            current_stage_instance_id=record.current_stage_instance_id,
            pending_action_kind=record.pending_action_kind,
            state_reason=record.state_reason,
            requested_action=record.requested_action,
        )
