"""HTTP schemas for automatic Dima Todo import configuration and progress."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from agentclaw.community.core.task.todos.auto_import_models import (
    DEFAULT_TODO_AUTO_IMPORT_CRON_EXPRESSION,
    DEFAULT_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
    MAX_TODO_AUTO_IMPORT_CONTAINERS,
    MAX_TODO_AUTO_IMPORT_CRON_LENGTH,
    MAX_TODO_AUTO_IMPORT_FILTER_LENGTH,
    MAX_TODO_AUTO_IMPORT_PROGRESS_DETAIL_LENGTH,
    MAX_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
    MIN_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
    TodoAutoImportProgressRecord,
    TodoAutoImportRuleRecord,
)
from agentclaw.community.core.task.todos.models import MAX_BOT_ID_LENGTH
from agentclaw.community.core.task.todos.source_models import (
    MAX_TODO_SOURCE_CONTAINER_ID_LENGTH,
    MAX_TODO_SOURCE_ITEM_ID_LENGTH,
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
    scheduler_bot_id: str = Field(
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="Creator-owned Bot that executes the scheduled automation.",
    )
    trigger_interval_seconds: int = Field(
        default=DEFAULT_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
        ge=MIN_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
        le=MAX_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
        description="Interval in seconds between automatic import triggers.",
    )
    cron_expression: str = Field(
        default=DEFAULT_TODO_AUTO_IMPORT_CRON_EXPRESSION,
        min_length=1,
        max_length=MAX_TODO_AUTO_IMPORT_CRON_LENGTH,
        description="Five-field Cron expression used by the external Agent scheduler.",
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
    scheduler_bot_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_BOT_ID_LENGTH,
        description="Replacement creator-owned scheduled-task executor Bot.",
    )
    trigger_interval_seconds: int | None = Field(
        default=None,
        ge=MIN_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
        le=MAX_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
        description="Replacement interval between automatic import triggers.",
    )
    cron_expression: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_TODO_AUTO_IMPORT_CRON_LENGTH,
        description="Replacement five-field Cron expression.",
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
    scheduler_bot_id: str | None = Field(
        description="Bot that executes this scheduled automation, or null for legacy rules."
    )
    trigger_interval_seconds: int = Field(description="Trigger interval in seconds.")
    cron_expression: str = Field(description="Five-field external scheduling expression.")
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
            scheduler_bot_id=record.scheduler_bot_id,
            trigger_interval_seconds=record.trigger_interval_seconds,
            cron_expression=record.cron_expression,
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
