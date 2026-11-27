"""HTTP schemas for independent Dima work-item batch imports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.core.task.todos.models import (
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
)
from agentclaw.community.core.task.todos.product_creation_models import (
    TodoDimaImportResult,
)

from .schemas import (
    CreateTodoExecutorRequest,
    CreateTodoOwnerRequest,
    CreateTodoSourceRequest,
)


class TodoDimaImportItemRequest(BaseModel):
    """One independently imported Dima work item."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        description="Todo title normalized from this Dima work item.",
    )
    description: str = Field(
        max_length=MAX_DESCRIPTION_LENGTH,
        description="Todo objective derived from this Dima work item.",
    )
    source: CreateTodoSourceRequest = Field(
        description="Stable Dima identity for this item.",
    )


class ImportDimaTodosRequest(BaseModel):
    """Import at most twenty Dima work items as independent product Todos."""

    model_config = ConfigDict(extra="forbid")

    space_id: int = Field(gt=0, description="Target Product Space.")
    executor: CreateTodoExecutorRequest = Field(
        description="Single Bot or frozen Bot-team revision for every item.",
    )
    owner: CreateTodoOwnerRequest = Field(
        description="Human responsible for all Todos in this batch.",
    )
    items: list[TodoDimaImportItemRequest] = Field(
        min_length=1,
        max_length=20,
        description="One to twenty Dima work items; each result is independent.",
    )


class ImportDimaTodosResultResponse(BaseModel):
    """One independently committed Dima import outcome."""

    source_work_item_id: str = Field(description="Stable Dima work-item identifier.")
    status: Literal["imported", "duplicate", "failed"] = Field(
        description="Per-item outcome; batch success never changes this field."
    )
    todo_id: str | None = Field(
        default=None,
        description="Existing Todo ID when it was imported or already bound to source.",
    )
    product_runtime_status: Literal[
        "ready",
        "retryable_failure",
        "not_applicable",
        "binding_conflict",
        "invalid_binding",
    ] = Field(description="Dynamic Product runtime binding result.")
    error_code: str | None = Field(
        default=None,
        description="Stable machine-readable error code when status is failed.",
    )
    retryable: bool = Field(
        default=False,
        description="Whether retrying the same source item can productively continue.",
    )

    @classmethod
    def from_record(
        cls, record: TodoDimaImportResult
    ) -> "ImportDimaTodosResultResponse":
        return cls(
            source_work_item_id=record.source_work_item_id,
            status=record.status.value,
            todo_id=record.todo_id,
            product_runtime_status=record.product_runtime_status.value,
            error_code=record.error_code,
            retryable=record.retryable,
        )


class ImportDimaTodosResponse(BaseModel):
    """Per-item Dima import results in request order."""

    items: list[ImportDimaTodosResultResponse] = Field(
        description="Independent outcomes, one for each requested Dima work item."
    )

    @classmethod
    def from_records(
        cls, records: tuple[TodoDimaImportResult, ...]
    ) -> "ImportDimaTodosResponse":
        return cls(
            items=[
                ImportDimaTodosResultResponse.from_record(record) for record in records
            ]
        )
