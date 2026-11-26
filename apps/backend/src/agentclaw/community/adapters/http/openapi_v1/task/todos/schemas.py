"""HTTP request and response schemas for the Todo workbench."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum
from agentclaw.community.core.task.todos.artifact_models import (
    TodoArtifactKind,
    TodoArtifactRecord,
)
from agentclaw.community.core.task.todos.auto_import_models import (
    MAX_TODO_AUTO_IMPORT_CONTAINERS,
    MAX_TODO_AUTO_IMPORT_FILTER_LENGTH,
    MAX_TODO_AUTO_IMPORT_PROGRESS_DETAIL_LENGTH,
    TodoAutoImportProgressRecord,
    TodoAutoImportRuleRecord,
)
from agentclaw.community.core.task.todos.models import (
    MAX_ACCEPTANCE_ID_LENGTH,
    MAX_ACCEPTANCE_TARGETS,
    MAX_ARTIFACT_ID_LENGTH,
    MAX_BOT_ID_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_EVENT_ID_LENGTH,
    MAX_INTERACTION_MENTIONS,
    MAX_MESSAGE_LENGTH,
    MAX_TITLE_LENGTH,
    TodoEventRecord,
    TodoRecord,
)
from agentclaw.community.core.task.todos.source_models import (
    MAX_TODO_SOURCE_CONTAINER_ID_LENGTH,
    MAX_TODO_SOURCE_ITEM_ID_LENGTH,
    MAX_TODO_SOURCE_UPDATED_AT_LENGTH,
    MAX_TODO_SOURCE_URL_LENGTH,
    TodoSourceDraft,
    TodoSourceProject,
    TodoSourceWorkItem,
    normalize_todo_source_metadata,
)
from agentclaw.community.core.task.todos.stage_models import TodoStageRecord


class TodoSourceProjectResponse(BaseModel):
    """One Dima workspace or project visible to the authenticated Human."""

    id: str = Field(description="Stable Dima workspace or project identifier.")
    name: str = Field(description="Human-readable Dima workspace or project name.")
    kind: Literal["workspace", "project"] = Field(
        description="Whether this selectable container is a workspace or project."
    )

    @classmethod
    def from_record(cls, record: TodoSourceProject) -> "TodoSourceProjectResponse":
        return cls(id=record.id, name=record.name, kind=record.kind)


class TodoSourceWorkItemResponse(BaseModel):
    """One normalized Dima Req, Task, or Bug selectable for import."""

    id: str = Field(description="Stable Dima work-item identifier.")
    project_id: str = Field(description="Dima project containing the work item.")
    category: Literal["req", "task", "bug"] = Field(
        description="Normalized Dima work-item category."
    )
    title: str = Field(description="Current Dima work-item title.")
    status: str | None = Field(
        default=None,
        description="Provider status label when Dima returned one.",
    )
    updated_at: str | None = Field(
        default=None,
        description="Provider update timestamp when Dima returned one.",
    )

    @classmethod
    def from_record(cls, record: TodoSourceWorkItem) -> "TodoSourceWorkItemResponse":
        return cls(
            id=record.id,
            project_id=record.project_id,
            category=record.category,
            title=record.title,
            status=record.status,
            updated_at=record.updated_at,
        )


class TodoSourceDraftResponse(BaseModel):
    """Editable Todo fields normalized from one Dima work item."""

    source_type: Literal["dima"] = Field(
        description="Source adapter that produced this editable draft."
    )
    project_id: str = Field(description="Dima project containing the source item.")
    work_item_id: str = Field(description="Dima work-item identifier.")
    category: Literal["req", "task", "bug"] = Field(
        description="Normalized Dima work-item category."
    )
    title: str = Field(description="Editable Todo title derived from Dima.")
    description: str = Field(
        description="Editable Todo description derived from Dima details."
    )
    source_url: str | None = Field(
        default=None,
        description="Dima work-item URL when the provider returned one.",
    )
    source_updated_at: str | None = Field(
        default=None,
        description="Dima update timestamp when the provider returned one.",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Whitelisted Dima field snapshot, such as processor identity.",
    )

    @classmethod
    def from_record(cls, record: TodoSourceDraft) -> "TodoSourceDraftResponse":
        return cls(
            source_type=record.source_type,
            project_id=record.project_id,
            work_item_id=record.work_item_id,
            category=record.category,
            title=record.title,
            description=record.description,
            source_url=record.source_url,
            source_updated_at=record.source_updated_at,
            metadata=record.metadata,
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


class CreateTodoSourceRequest(BaseModel):
    """Stable external-source identity for an imported Todo."""

    type: Literal["dima"] = Field(description="External source adapter.")
    container_id: str = Field(
        min_length=1,
        max_length=MAX_TODO_SOURCE_CONTAINER_ID_LENGTH,
        description="Dima project containing the selected work item.",
    )
    item_id: str = Field(
        min_length=1,
        max_length=MAX_TODO_SOURCE_ITEM_ID_LENGTH,
        description="Stable Dima work-item identifier used for duplicate prevention.",
    )
    url: str | None = Field(
        default=None,
        max_length=MAX_TODO_SOURCE_URL_LENGTH,
        description="Optional Dima work-item URL snapshot.",
    )
    updated_at: str | None = Field(
        default=None,
        max_length=MAX_TODO_SOURCE_UPDATED_AT_LENGTH,
        description="Optional Dima update timestamp snapshot.",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Bounded whitelisted Dima field snapshot.",
    )

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> dict[str, str]:
        return normalize_todo_source_metadata(value)


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
    source: CreateTodoSourceRequest | None = Field(
        default=None,
        description="Imported-source identity; omitted for manually created Todos.",
    )


class CreateTodoFromAutoImportRuleRequest(BaseModel):
    """Create one Todo from a Dima item selected by an automatic import rule."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        description="Todo title derived from the selected Dima work item.",
    )
    description: str = Field(
        max_length=MAX_DESCRIPTION_LENGTH,
        description="Todo objective derived from the selected Dima work item.",
    )
    source: CreateTodoSourceRequest = Field(
        description="Dima source identity and whitelisted source-field snapshot."
    )


class CreateTodoAutoImportRuleRequest(BaseModel):
    """Create one automatic Dima import rule for an external Agent."""

    space_id: int = Field(gt=0, description="Product Space that owns imported Todos.")
    source_container_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_TODO_AUTO_IMPORT_CONTAINERS,
        description="Dima workspace or project identifiers scanned by the Agent.",
    )
    filter_text: str = Field(
        min_length=1,
        max_length=MAX_TODO_AUTO_IMPORT_FILTER_LENGTH,
        description="Natural-language filter interpreted by the external Agent.",
    )
    bot_id: str = Field(
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="Space-eligible Bot assigned to imported Todos.",
    )


class UpdateTodoAutoImportRuleRequest(BaseModel):
    """Patch mutable configuration or enablement of one import rule."""

    source_container_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_TODO_AUTO_IMPORT_CONTAINERS,
        description="Replacement Dima workspace or project identifiers.",
    )
    filter_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_TODO_AUTO_IMPORT_FILTER_LENGTH,
        description="Replacement natural-language filter.",
    )
    bot_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="Replacement Space-eligible Bot.",
    )
    status: Literal["enabled", "disabled"] | None = Field(
        default=None,
        description="Whether the external Agent should scan this rule.",
    )


class TodoAutoImportRuleResponse(BaseModel):
    """Saved automatic Dima import configuration and its scan cursor."""

    rule_id: str = Field(description="Stable automatic import rule identifier.")
    space_id: int = Field(description="Product Space that owns imported Todos.")
    source_container_ids: list[str] = Field(
        description="Dima workspace or project identifiers scanned by the Agent."
    )
    filter_text: str = Field(
        description="Natural-language filter interpreted by the external Agent."
    )
    bot_id: str = Field(description="Bot assigned to Todos created by this rule.")
    status: Literal["enabled", "disabled"] = Field(
        description="Whether the external Agent should scan this rule."
    )
    cursor_updated_at: str | None = Field(
        description="Latest fully scanned Dima update time, or null before the first scan."
    )
    created_at: str = Field(description="Rule creation time in ISO 8601 format.")
    updated_at: str = Field(description="Last rule update time in ISO 8601 format.")

    @classmethod
    def from_record(
        cls, record: TodoAutoImportRuleRecord
    ) -> "TodoAutoImportRuleResponse":
        return cls(
            rule_id=record.rule_id,
            space_id=record.space_id,
            source_container_ids=list(record.source_container_ids),
            filter_text=record.filter_text,
            bot_id=record.bot_id,
            status=record.status.value,
            cursor_updated_at=(
                _iso_datetime(record.cursor_updated_at)
                if record.cursor_updated_at is not None
                else None
            ),
            created_at=_iso_datetime(record.created_at),
            updated_at=_iso_datetime(record.updated_at),
        )


class TodoAutoImportCheckpointRequest(BaseModel):
    """Record one source-version result and optionally advance the scan cursor."""

    source_container_id: str = Field(
        min_length=1,
        max_length=MAX_TODO_SOURCE_CONTAINER_ID_LENGTH,
        description="Configured Dima workspace or project containing the source item.",
    )
    source_item_id: str = Field(
        min_length=1,
        max_length=MAX_TODO_SOURCE_ITEM_ID_LENGTH,
        description="Stable Dima work-item identifier.",
    )
    source_updated_at: datetime = Field(
        description="Timezone-aware update time of the processed Dima work-item version."
    )
    status: Literal["imported", "skipped", "failed", "duplicate"] = Field(
        description="Outcome recorded for this Dima work-item version."
    )
    todo_id: str | None = Field(
        default=None,
        max_length=64,
        description="Created or existing Todo identifier for imported or duplicate outcomes.",
    )
    detail: str = Field(
        default="",
        max_length=MAX_TODO_AUTO_IMPORT_PROGRESS_DETAIL_LENGTH,
        description="Optional diagnostic detail about the processing outcome.",
    )
    cursor_updated_at: datetime | None = Field(
        default=None,
        description="Optional fully scanned update time used to advance the rule cursor.",
    )


class TodoAutoImportProgressResponse(BaseModel):
    """Persisted retry-safe outcome for one Dima work-item version."""

    progress_id: str = Field(description="Stable checkpoint record identifier.")
    rule_id: str = Field(description="Automatic import rule that owns this checkpoint.")
    source_container_id: str = Field(
        description="Dima workspace or project containing the source item."
    )
    source_item_id: str = Field(description="Stable Dima work-item identifier.")
    source_updated_at: str = Field(
        description="Processed source update time in ISO 8601 format."
    )
    status: Literal["imported", "skipped", "failed", "duplicate"] = Field(
        description="Persisted processing outcome for this source version."
    )
    todo_id: str | None = Field(
        description="Created or existing Todo identifier when applicable."
    )
    detail: str = Field(description="Diagnostic detail recorded by the external Agent.")
    created_at: str = Field(description="Checkpoint creation time in ISO 8601 format.")
    updated_at: str = Field(
        description="Last checkpoint update time in ISO 8601 format."
    )

    @classmethod
    def from_record(
        cls, record: TodoAutoImportProgressRecord
    ) -> "TodoAutoImportProgressResponse":
        return cls(
            progress_id=record.progress_id,
            rule_id=record.rule_id,
            source_container_id=record.source_container_id,
            source_item_id=record.source_item_id,
            source_updated_at=_iso_datetime(record.source_updated_at),
            status=record.status.value,
            todo_id=record.todo_id,
            detail=record.detail,
            created_at=_iso_datetime(record.created_at),
            updated_at=_iso_datetime(record.updated_at),
        )


def _iso_datetime(value: datetime) -> str:
    normalized = (
        value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    )
    return normalized.isoformat()


class TodoInteractionMentionRequest(BaseModel):
    """One structured Bot mention in a product interaction."""

    actor_type: Literal["bot"] = Field(
        description="Mentioned actor kind; Todo interactions currently support Bots only."
    )
    actor_id: str = Field(
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="Identifier of a Bot eligible in the Todo Space.",
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

    title: str = Field(
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        description="Concrete Todo title shown in the workbench.",
    )
    description: str = Field(
        max_length=MAX_DESCRIPTION_LENGTH,
        description="Concrete objective executed through the selected template flow.",
    )
    template_id: str = Field(
        min_length=1,
        max_length=64,
        description="Stable Todo template identity selected for this Todo.",
    )
    template_version_id: str = Field(
        min_length=1,
        max_length=64,
        description="Exact published template version pinned to this Todo.",
    )
    participant_bindings: dict[str, TodoParticipantBindingRequest] = Field(
        description="Values for every participant slot required by the template."
    )


class CompleteTodoStageRequest(BaseModel):
    """Complete the current Human stage with an auditable result."""

    summary: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_LENGTH,
        description="Human-readable completion summary recorded for the stage.",
    )
    idempotency_key: str = Field(
        min_length=1,
        max_length=256,
        description="Caller-stable key that makes stage completion retry-safe.",
    )
    result: dict[str, object] = Field(
        default_factory=dict,
        description="Optional structured completion result supplied by the Human.",
    )


class CancelTodoRequest(BaseModel):
    """Explicit Human cancellation request."""

    reason: str | None = Field(
        default=None,
        max_length=MAX_MESSAGE_LENGTH,
        description="Optional Human-readable reason for cancelling the Todo.",
    )


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

    __descriptions__ = {
        "planned_human_stage": "The template intentionally requires its bound Human to act.",
        "blocked": "Execution cannot continue until the requested action is resolved.",
    }


class TodoStageResponse(BaseModel):
    """Expanded stage timeline item returned for a Todo."""

    stage_instance_id: str = Field(description="Stable expanded stage identifier.")
    todo_id: str = Field(description="Todo that owns this expanded stage.")
    template_version_id: str = Field(
        description="Immutable template version from which the stage was expanded."
    )
    stage_key: str = Field(
        description="Version-local stable key of the source stage definition."
    )
    stage_order: int = Field(description="One-based execution order within the Todo.")
    stage_name: str = Field(description="Human-readable stage name snapshot.")
    purpose: str = Field(description="Reusable product purpose of this stage.")
    stage_kind: str = Field(description="Execution kind, either bot or human.")
    executor_binding_key: str | None = Field(
        description="Participant binding key used to resolve the executor, if any."
    )
    executor_subject_type: str = Field(
        description="Resolved executor identity kind, either bot or human."
    )
    executor_subject_id: str = Field(description="Resolved executor identity.")
    executor_subject_name: str = Field(description="Resolved executor display name.")
    owner_binding_key: str | None = Field(
        description="Participant binding key used to resolve the Human owner, if any."
    )
    owner_user_id: str = Field(description="Resolved Human owner identity.")
    owner_user_name: str = Field(description="Resolved Human owner display name.")
    completion_gate: dict[str, object] = Field(
        description="Deterministic hard artifact gates required for completion."
    )
    status: TodoStageStatusSchema = Field(description="Current stage execution state.")
    collab_context_id: str | None = Field(
        description="Collaboration Context for this stage execution, if dispatched."
    )
    pending_action_kind: TodoStagePendingActionSchema | None = Field(
        description="Reason this stage currently requires product action, if any."
    )
    state_reason: str | None = Field(
        description="Human-readable explanation of the current waiting state, if any."
    )
    requested_action: str | None = Field(
        description="Action requested from a Human before execution can continue, if any."
    )
    started_at: str | None = Field(
        description="ISO-8601 timestamp when the stage first started, if started."
    )
    completed_at: str | None = Field(
        description="ISO-8601 timestamp when the stage completed, if completed."
    )
    created_at: str = Field(description="ISO-8601 stage creation timestamp.")
    updated_at: str = Field(description="ISO-8601 last-modified timestamp.")

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
            completed_at=record.completed_at.isoformat()
            if record.completed_at
            else None,
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
    reply_to_event_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_EVENT_ID_LENGTH,
        description="Stable interaction event being replied to; floor numbers are presentation-only.",
    )
    mentions: list[TodoInteractionMentionRequest] = Field(
        default_factory=list,
        max_length=MAX_INTERACTION_MENTIONS,
        description="Structured Bot mentions; persistence does not wake or run the mentioned Bots.",
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
    state_reason: str | None = Field(
        description="Current product-state reason, if any."
    )
    requested_action: str | None = Field(
        description="Action requested from the Human, if any."
    )

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
