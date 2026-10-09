"""Semantic execution events shared by centralized mode and recovery."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskSemanticEventType(StrEnum):
    PLAN_REQUESTED = "PLAN_REQUESTED"
    DISPATCH_REQUESTED = "DISPATCH_REQUESTED"
    EXECUTION_REQUESTED = "EXECUTION_REQUESTED"


@dataclass(frozen=True)
class TaskSemanticEvent:
    event_id: str
    event_type: TaskSemanticEventType
    task_id: str
    node_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
