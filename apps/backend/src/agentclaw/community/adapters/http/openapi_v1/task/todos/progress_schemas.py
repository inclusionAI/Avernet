"""Stable HTTP schemas for the Todo Product Progress projection."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agentclaw.community.core.task.todos.models import TodoStatus
from agentclaw.community.core.task.todos.progress_models import (
    TodoProgress,
    TodoStageProgress,
)

TodoProgressRuntimeKind = Literal["product_collet", "product_runtime_pending", "legacy_context", "invalid_binding"]
TodoProgressStageStatusSchema = Literal[
    "not_started",
    "in_progress",
    "pending_action",
    "interrupted",
    "completed",
    "cancelled",
    "unknown",
]


class TodoProgressStepResponse(BaseModel):
    """One normalized session message rendered as a sub-step."""

    step_id: str = Field(description="Stable message identity or deterministic fallback.")
    name: str = Field(description="Concise step summary.")
    status: TodoProgressStageStatusSchema = Field(description="Sub-step state.")
    detail: str = Field(description="Bounded safe text from the runtime message.")
    occurred_at: str | None = Field(
        default=None,
        description="Reliable message timestamp when the provider supplied one.",
    )


class TodoProgressOutputResponse(BaseModel):
    """Artifact reference; preview/download always uses the Todo Artifact API."""

    artifact_id: str | None = Field(
        default=None,
        description="Todo artifact identity when the runtime reference is resolvable.",
    )
    kind: str = Field(description="Runtime artifact kind.")
    name: str = Field(description="Human-readable artifact name.")
    summary: str = Field(description="Bounded artifact summary.")


class TodoProgressBlockerResponse(BaseModel):
    """Human-actionable blockage information."""

    reason: str | None = Field(default=None, description="Why the stage is blocked.")
    requested_action: str | None = Field(
        default=None,
        description="Action requested from the responsible Human.",
    )
    error_code: str | None = Field(
        default=None,
        description="Bounded runtime failure text when available.",
    )


class TodoStageProgressResponse(BaseModel):
    """Runtime projection for one stage fixed in the Todo snapshot."""

    stage_instance_id: str = Field(description="Stable Todo stage instance identity.")
    code: str = Field(description="Stable stage code matching the Product Profile.")
    name: str = Field(description="Human-readable stage name snapshot.")
    order: int = Field(description="Order fixed when the Todo was created.")
    kind: str = Field(description="Stage kind: bot or human.")
    status: TodoProgressStageStatusSchema = Field(description="Product stage state.")
    owner_user_id: str = Field(description="Responsible Human identity.")
    owner_user_name: str = Field(description="Responsible Human display name.")
    executor_subject_id: str = Field(description="Resolved executor identity.")
    executor_subject_name: str = Field(description="Resolved executor display name.")
    summary: str = Field(description="Deterministic stage summary.")
    current_step: str | None = Field(
        default=None,
        description="Latest normalized sub-step, when this stage's details were loaded.",
    )
    steps: list[TodoProgressStepResponse] = Field(
        default_factory=list,
        description="Loaded session-message sub-steps in provider order.",
    )
    outputs: list[TodoProgressOutputResponse] = Field(
        default_factory=list,
        description="Artifact references produced by this stage.",
    )
    blocker: TodoProgressBlockerResponse | None = Field(
        default=None,
        description="Current blocker requiring Human action, if any.",
    )
    attempt_count: int = Field(description="Number of runtime attempts for this stage.")
    details_loaded: bool = Field(
        description="Whether session details were loaded for this response."
    )
    messages_complete: bool | None = Field(
        default=None,
        description="Runtime completeness fact; null means completeness is unknown.",
    )
    ignored_message_count: int = Field(
        description="Number of malformed or unsupported messages safely ignored."
    )
    updated_at: str | None = Field(
        default=None,
        description="Last reliable stage update timestamp.",
    )

    @classmethod
    def from_record(cls, record: TodoStageProgress) -> "TodoStageProgressResponse":
        return cls(
            stage_instance_id=record.stage_instance_id,
            code=record.code,
            name=record.name,
            order=record.order,
            kind=record.kind,
            status=record.status.value,
            owner_user_id=record.owner_user_id,
            owner_user_name=record.owner_user_name,
            executor_subject_id=record.executor_subject_id,
            executor_subject_name=record.executor_subject_name,
            summary=record.summary,
            current_step=record.current_step,
            steps=[
                TodoProgressStepResponse(
                    step_id=step.step_id,
                    name=step.name,
                    status=step.status.value,
                    detail=step.detail,
                    occurred_at=step.occurred_at,
                )
                for step in record.steps
            ],
            outputs=[
                TodoProgressOutputResponse(
                    artifact_id=output.artifact_id,
                    kind=output.kind,
                    name=output.name,
                    summary=output.summary,
                )
                for output in record.outputs
            ],
            blocker=(
                TodoProgressBlockerResponse(
                    reason=record.blocker.reason,
                    requested_action=record.blocker.requested_action,
                    error_code=record.blocker.error_code,
                )
                if record.blocker is not None
                else None
            ),
            attempt_count=record.attempt_count,
            details_loaded=record.details_loaded,
            messages_complete=record.messages_complete,
            ignored_message_count=record.ignored_message_count,
            updated_at=record.updated_at,
        )


class TodoProgressResponse(BaseModel):
    """Complete progress projection for one Product or Legacy Todo."""

    todo_id: str = Field(description="A-owned Todo identifier.")
    status: str = Field(description="Persisted Todo product state.")
    runtime_kind: TodoProgressRuntimeKind = Field(
        description="Authoritative Todo runtime mode."
    )
    runtime_unavailable: bool = Field(
        description="Whether Product runtime facts could not be read."
    )
    runtime_error_code: str | None = Field(
        default=None,
        description="Stable partial-data error, if any.",
    )
    current_stage_code: str | None = Field(
        default=None,
        description="Current actionable stage code, if any.",
    )
    rollout_revision: int | None = Field(
        default=None,
        description="Product rollout revision when runtime facts are available.",
    )
    stopped: bool | None = Field(
        default=None,
        description="Product runtime stopped fact when available.",
    )
    stages: list[TodoStageProgressResponse] = Field(
        description="Product-owned stage skeleton with runtime state."
    )

    @classmethod
    def from_record(cls, record: TodoProgress) -> "TodoProgressResponse":
        return cls(
            todo_id=record.todo_id,
            status=record.status.value
            if isinstance(record.status, TodoStatus)
            else str(record.status),
            runtime_kind=(
                record.runtime_kind.value
                if hasattr(record.runtime_kind, "value")
                else record.runtime_kind
            ),
            runtime_unavailable=record.runtime_unavailable,
            runtime_error_code=record.runtime_error_code,
            current_stage_code=record.current_stage_code,
            rollout_revision=record.rollout_revision,
            stopped=record.stopped,
            stages=[
                TodoStageProgressResponse.from_record(stage) for stage in record.stages
            ],
        )
