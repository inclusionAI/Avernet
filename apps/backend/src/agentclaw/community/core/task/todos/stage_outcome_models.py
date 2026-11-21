"""Internal stage transition facts used by Todo lifecycle services."""

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
    """A bounded internal result of one stage transition, not a Bot-facing protocol."""

    todo_id: str
    stage_instance_id: str
    outcome: TodoStageOutcomeKind
    summary: str
    execution_id: str | None = None
    idempotency_key: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    blocker_reason: str | None = None
    requested_action: str | None = None
