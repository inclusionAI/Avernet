"""Transport-agnostic Todo workbench records and input limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

MAX_TODO_ID_LENGTH = 64
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 4_000
MAX_BOT_ID_LENGTH = 256
MAX_BOT_NAME_LENGTH = 256
MAX_ACCEPTANCE_ID_LENGTH = 64
MAX_ACCEPTANCE_TARGETS = 100
MAX_MESSAGE_LENGTH = 4_000
MAX_COLLAB_CONTEXT_ID_LENGTH = 256
MAX_EVENT_ID_LENGTH = 256
MAX_EVENT_TYPE_LENGTH = 128
MAX_ARTIFACT_ID_LENGTH = 256
MAX_ARTIFACT_NAME_LENGTH = 256
MAX_ARTIFACT_URL_LENGTH = 2_048
MAX_ACTOR_ID_LENGTH = 256
MAX_ACTOR_NAME_LENGTH = 256
MAX_INTERACTION_MENTIONS = 20


class TodoStatus(StrEnum):
    """Product states that can be persisted on a Todo resource."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    INTERRUPTED = "interrupted"
    PENDING_ACTION = "pending_action"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    # Source compatibility for callers compiled against the pre-template API.
    # These aliases never create additional persisted/product states.
    PENDING = NOT_STARTED
    PENDING_ACCEPTANCE = PENDING_ACTION


# Public response schemas and workbench filters use this explicit order.
TODO_STATUS_ORDER: tuple[TodoStatus, ...] = (
    TodoStatus.NOT_STARTED,
    TodoStatus.IN_PROGRESS,
    TodoStatus.PENDING_ACTION,
    TodoStatus.INTERRUPTED,
    TodoStatus.COMPLETED,
    TodoStatus.CANCELLED,
)


class AcceptanceVerdict(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class TodoAcceptanceTargetKind(StrEnum):
    EVENT = "event"
    ARTIFACT = "artifact"


class TodoInteractionMentionActorType(StrEnum):
    BOT = "bot"


@dataclass(frozen=True)
class TodoInteractionMention:
    """Structured product mention persisted in a Human interaction event."""

    actor_type: TodoInteractionMentionActorType
    actor_id: str


@dataclass(frozen=True)
class TodoAcceptanceTarget:
    """Stable problem reference selected from the interaction projection."""

    kind: TodoAcceptanceTargetKind
    target_id: str
    floor: int


@dataclass(frozen=True)
class TodoRecord:
    """The product Todo owned by A; execution details stay in events."""

    todo_id: str
    space_id: int
    title: str
    description: str
    bot_id: str
    bot_name: str
    status: TodoStatus
    collab_context_id: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    template_id: str = ""
    template_version_id: str = ""
    template_name_snapshot: str = ""
    current_stage_instance_id: str | None = None
    pending_action_kind: str | None = None
    state_reason: str | None = None
    requested_action: str | None = None


@dataclass(frozen=True)
class TodoPage:
    total: int
    items: tuple[TodoRecord, ...]


@dataclass(frozen=True)
class TodoArtifactRecord:
    artifact_id: str | None
    name: str
    url: str


@dataclass(frozen=True)
class TodoEventRecord:
    """Projection of one B collaboration event for the HTTP workbench API."""

    event_id: str
    event_seq: int
    event_type: str
    actor_id: str
    actor_type: str
    content: str
    execution_id: str
    context_version: int | None
    status: str | None
    artifacts: tuple[TodoArtifactRecord, ...]
    created_at: str
    payload: dict[str, object] = field(default_factory=dict)
