"""Transport-neutral records for resolved Todo stages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TodoStageStatus(StrEnum):
    """Execution states persisted for one expanded Todo stage."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class TodoPendingActionKind(StrEnum):
    """Explicit reason a Todo is waiting for product action."""

    PLANNED_HUMAN_STAGE = "planned_human_stage"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class TodoParticipantBinding:
    """Concrete identity resolved from one template participant role."""

    key: str
    label: str
    subject_type: str
    subject_id: str
    subject_name: str


@dataclass(frozen=True)
class TodoParticipantBindingInput:
    """Caller-provided identity used to fill a template participant slot."""

    subject_type: str
    subject_id: str
    subject_name: str | None = None


@dataclass(frozen=True)
class TodoStageRecord:
    """One concrete ordered stage fixed at Todo creation."""

    stage_instance_id: str
    todo_id: str
    template_version_id: str
    stage_key: str
    stage_order: int
    stage_name: str
    purpose: str
    stage_kind: str
    executor_binding_key: str | None
    executor_subject_type: str
    executor_subject_id: str
    executor_subject_name: str
    owner_binding_key: str | None
    owner_user_id: str
    owner_user_name: str
    completion_gate_json: str
    status: TodoStageStatus
    collab_context_id: str | None
    pending_action_kind: TodoPendingActionKind | None
    state_reason: str | None
    requested_action: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_outcome_key: str | None = None
