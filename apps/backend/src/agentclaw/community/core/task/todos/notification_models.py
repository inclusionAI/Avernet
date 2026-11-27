"""Stable Todo notification facts owned by the Todo product boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TodoNotificationType(StrEnum):
    """Product reasons that may notify one responsible or explicit Human."""

    HUMAN_ACTION_REQUIRED = "human_action_required"
    BOT_INFORMATION_REQUIRED = "bot_information_required"
    EXECUTION_INTERRUPTED = "execution_interrupted"
    VERIFICATION_REQUIRED = "verification_required"
    TODO_COMPLETED = "todo_completed"


@dataclass(frozen=True)
class TodoNotificationEvent:
    """One bounded Todo notification fact and its stable delivery identity."""

    delivery_key: str
    notification_type: TodoNotificationType
    todo_id: str
    space_id: str
    recipient_id: str | None
    title: str
    summary: str
    stage_code: str | None
    stage_rollout_id: int | None
    rollout_revision: int | None
    action_version: str
    occurred_at: datetime


class TodoNotificationPublishStatus(StrEnum):
    """Outcomes deliberately defined by Todo, not the Forum domain."""

    PUBLISHED = "published"
    ALREADY_PUBLISHED = "already_published"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class TodoNotificationPublishResult:
    """Bounded publication result; callers must not echo raw errors."""

    status: TodoNotificationPublishStatus
    reason: str | None = None


def build_todo_notification_delivery_key(
    *,
    todo_id: str,
    notification_type: TodoNotificationType,
    rollout_revision: int | None,
    stage_rollout_id: int | None,
    action_version: str,
) -> str:
    """Build one stable key for the same persisted action fact."""

    return ":".join(
        (
            todo_id,
            notification_type.value,
            str(rollout_revision) if rollout_revision is not None else "none",
            str(stage_rollout_id) if stage_rollout_id is not None else "none",
            action_version,
        )
    )
