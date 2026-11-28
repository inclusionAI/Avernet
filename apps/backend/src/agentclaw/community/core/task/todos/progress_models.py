"""Stable product projection of Product runtime progress for one Todo."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentclaw.community.core.task.todos.models import TodoStatus
from agentclaw.community.core.task.todos.product_creation_models import (
    TodoRuntimeKind,
)


class TodoProgressStageStatus(StrEnum):
    """Product-facing state of one Todo stage, independent of Todo global state."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PENDING_ACTION = "pending_action"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TodoProgressStep:
    """One normalized Bot/Human execution message projected as a sub-step."""

    step_id: str
    name: str
    status: TodoProgressStageStatus
    detail: str
    occurred_at: str | None = None


@dataclass(frozen=True)
class TodoProgressOutput:
    """A concise product artifact reference; content is read through the artifact API."""

    artifact_id: str | None
    kind: str
    name: str
    summary: str


@dataclass(frozen=True)
class TodoProgressBlocker:
    """Why one stage cannot continue and what the Human is asked to do."""

    reason: str | None
    requested_action: str | None
    error_code: str | None


@dataclass(frozen=True)
class TodoStageProgress:
    """Runtime-aware projection of one stage fixed in the Todo snapshot."""

    stage_instance_id: str
    code: str
    name: str
    order: int
    kind: str
    status: TodoProgressStageStatus
    owner_user_id: str
    owner_user_name: str
    executor_subject_id: str
    executor_subject_name: str
    summary: str
    current_step: str | None
    steps: tuple[TodoProgressStep, ...] = ()
    outputs: tuple[TodoProgressOutput, ...] = ()
    blocker: TodoProgressBlocker | None = None
    attempt_count: int = 0
    details_loaded: bool = False
    messages_complete: bool | None = None
    ignored_message_count: int = 0
    updated_at: str | None = None


@dataclass(frozen=True)
class TodoProgress:
    """Backend-owned product progress value object consumed by the Todo detail page."""

    todo_id: str
    status: TodoStatus
    runtime_kind: TodoRuntimeKind
    runtime_unavailable: bool
    runtime_error_code: str | None
    stages: tuple[TodoStageProgress, ...]
    current_stage_code: str | None = None
    rollout_revision: int | None = None
    stopped: bool | None = None
