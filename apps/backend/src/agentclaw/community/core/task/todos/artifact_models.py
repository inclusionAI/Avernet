"""Transport-neutral Todo execution credentials and persisted artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

MAX_EXECUTION_ID_LENGTH = 64
MAX_EXECUTION_TOKEN_HASH_LENGTH = 64
MAX_ARTIFACT_CONTENT_LENGTH = 1_000_000
MAX_FILE_NAME_LENGTH = 512
MAX_CONTENT_TYPE_LENGTH = 256


class TodoExecutionStatus(StrEnum):
    ACTIVE = "active"
    REPLACED = "replaced"
    COMPLETED = "completed"


class TodoArtifactKind(StrEnum):
    TEXT = "text"
    FILE = "file"
    URL_LINK = "url_link"
    CODE = "code"


@dataclass(frozen=True)
class TodoExecutionRecord:
    execution_id: str
    todo_id: str
    collab_context_id: str
    run_id: str | None
    token_hash: str
    status: TodoExecutionStatus
    executor_id: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TodoExecutionCredential:
    """One-time plaintext credential returned only to the runtime caller."""

    execution_id: str
    token: str


@dataclass(frozen=True)
class TodoArtifactRecord:
    artifact_id: str
    todo_id: str
    collab_context_id: str
    execution_id: str
    artifact_name: str
    artifact_kind: TodoArtifactKind
    content: dict[str, Any]
    content_hash: str
    creator: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TodoArtifactDownload:
    record: TodoArtifactRecord
    payload: bytes
