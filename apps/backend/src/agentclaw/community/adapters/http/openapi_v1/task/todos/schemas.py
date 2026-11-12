"""HTTP request and response schemas for the Todo workbench."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactKind,
    TodoArtifactRecord,
)
from agentclaw.community.core.task.todos.models import (
    MAX_BOT_ID_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_MESSAGE_LENGTH,
    MAX_TITLE_LENGTH,
    TodoEventRecord,
    TodoRecord,
)


class TodoStatusSchema(_DocumentedEnum):
    """Product state owned by A while execution events stay in B."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PENDING_ACCEPTANCE = "pending_acceptance"
    COMPLETED = "completed"

    __descriptions__ = {
        "pending": "Waiting for automatic dispatch; no B Context is bound yet.",
        "in_progress": "A B Context has started executing with the selected Bot.",
        "pending_acceptance": "B completed execution and the result awaits Human acceptance.",
        "completed": "The Human approved the execution result.",
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


class CreateTodoInteractionRequest(BaseModel):
    """Append one Human interaction event in the B collaboration Context."""

    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_LENGTH,
        description="Feedback or supplementary instruction for the pending execution.",
    )


class SubmitTodoAcceptanceRequest(BaseModel):
    """Approve or reject the execution currently pending acceptance."""

    verdict: Literal["approved", "rejected"] = Field(
        description=(
            "Human decision for the pending execution: approve to complete it, "
            "or reject while keeping it pending for further Human interaction."
        )
    )
    message: str | None = Field(
        default=None,
        max_length=MAX_MESSAGE_LENGTH,
        description="Optional note recorded with the acceptance decision.",
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

    artifact_id: str
    todo_id: str
    execution_id: str
    artifact_name: str
    kind: TodoArtifactKind
    content: dict[str, object]
    created_at: str

    @classmethod
    def from_record(cls, record: TodoArtifactRecord) -> "TodoArtifactRecordResponse":
        content = dict(record.content)
        if record.artifact_kind is TodoArtifactKind.FILE:
            content.pop("storage_id", None)
        return cls(
            artifact_id=record.artifact_id,
            todo_id=record.todo_id,
            execution_id=record.execution_id,
            artifact_name=record.artifact_name,
            kind=record.artifact_kind,
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
        )
