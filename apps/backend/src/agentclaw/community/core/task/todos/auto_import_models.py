"""Product-owned configuration and progress records for external Dima import agents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

MAX_TODO_AUTO_IMPORT_RULE_ID_LENGTH = 64
MAX_TODO_AUTO_IMPORT_FILTER_LENGTH = 4_000
MAX_TODO_AUTO_IMPORT_CONTAINERS = 20
MAX_TODO_AUTO_IMPORT_PROGRESS_DETAIL_LENGTH = 2_000
MIN_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS = 1
MAX_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS = 86_400
MAX_TODO_AUTO_IMPORT_CRON_LENGTH = 128
DEFAULT_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS = 1_800
DEFAULT_TODO_AUTO_IMPORT_CRON_EXPRESSION = "*/30 * * * *"


class TodoAutoImportRuleStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class TodoAutoImportProgressStatus(str, Enum):
    IMPORTED = "imported"
    SKIPPED = "skipped"
    FAILED = "failed"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class TodoAutoImportRuleRecord:
    """One saved automatic Dima import rule owned by a Human."""

    rule_id: str
    space_id: int
    source_container_ids: tuple[str, ...]
    filter_text: str
    bot_id: str
    scheduler_bot_id: str | None
    trigger_interval_seconds: int
    cron_expression: str
    status: TodoAutoImportRuleStatus
    cursor_updated_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TodoAutoImportCheckpoint:
    """One source-version outcome plus an optional monotonic scan cursor."""

    source_container_id: str
    source_item_id: str
    source_updated_at: datetime
    status: TodoAutoImportProgressStatus
    todo_id: str | None = None
    detail: str = ""
    cursor_updated_at: datetime | None = None


@dataclass(frozen=True)
class TodoAutoImportProgressRecord:
    """Persisted per-item outcome used for audit and retry-safe agent work."""

    progress_id: str
    rule_id: str
    source_container_id: str
    source_item_id: str
    source_updated_at: datetime
    status: TodoAutoImportProgressStatus
    todo_id: str | None
    detail: str
    created_at: datetime
    updated_at: datetime


def normalize_source_container_ids(values: object) -> tuple[str, ...]:
    """Return a bounded, de-duplicated set while preserving UI order."""

    if not isinstance(values, (list, tuple)):
        raise ValueError("source_container_ids must be an array")
    if not values:
        raise ValueError("at least one source container is required")
    if len(values) > MAX_TODO_AUTO_IMPORT_CONTAINERS:
        raise ValueError("too many source containers")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("source container ids must be strings")
        container_id = value.strip()
        if not container_id:
            raise ValueError("source container id must not be empty")
        if container_id in seen:
            continue
        seen.add(container_id)
        normalized.append(container_id)
    if not normalized:
        raise ValueError("at least one source container is required")
    return tuple(normalized)
