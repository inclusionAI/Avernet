"""Stage outcome protocol shared by Bot and Human lifecycle commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TodoStageOutcomeKind(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class TodoStageOutcome:
    """A bounded result of one stage attempt, not a Todo product state."""

    todo_id: str
    stage_instance_id: str
    outcome: TodoStageOutcomeKind
    summary: str
    execution_id: str | None = None
    idempotency_key: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    blocker_reason: str | None = None
    requested_action: str | None = None
