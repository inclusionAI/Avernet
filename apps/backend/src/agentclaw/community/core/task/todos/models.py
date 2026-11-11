"""Transport-agnostic Todo workbench records and input limits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

MAX_TODO_ID_LENGTH = 64
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 4_000
MAX_BOT_ID_LENGTH = 256
MAX_BOT_NAME_LENGTH = 256
MAX_ACCEPTANCE_ID_LENGTH = 64
MAX_MESSAGE_LENGTH = 4_000
MAX_COLLAB_CONTEXT_ID_LENGTH = 256
MAX_EVENT_ID_LENGTH = 256
MAX_EVENT_TYPE_LENGTH = 128
MAX_ARTIFACT_ID_LENGTH = 256
MAX_ARTIFACT_NAME_LENGTH = 256
MAX_ARTIFACT_URL_LENGTH = 2_048
MAX_ACTOR_ID_LENGTH = 256
MAX_ACTOR_NAME_LENGTH = 256


class TodoStatus(StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    PENDING_ACCEPTANCE = "pending_acceptance"
    COMPLETED = "completed"


class AcceptanceVerdict(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class TodoRecord:
    """The product Todo owned by A; execution details stay in B events."""

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
