"""Transport-independent task intake state and completeness rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agentclaw.community.core.task.domain.models import TaskSourceType, TaskSpec


class IntakeTargetType(StrEnum):
    BOT = "BOT"
    GROUP = "GROUP"


@dataclass(frozen=True)
class IntakeTarget:
    target_type: IntakeTargetType
    target_id: str


class TaskIntakeAction(StrEnum):
    START = "START"
    RESUME = "RESUME"
    CONTINUE = "CONTINUE"
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"


@dataclass(frozen=True)
class TaskIntakeCommand:
    action: TaskIntakeAction
    message: str | None = None
    requested_target: IntakeTarget | None = None


@dataclass(frozen=True)
class TaskIntakeContext:
    session_id: str | None
    owner_user_id: str
    source_type: TaskSourceType
    caller_bot_id: str | None = None


class TaskIntakeStatus(StrEnum):
    CLARIFYING = "CLARIFYING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


@dataclass
class TaskIntakeResult:
    session_id: str
    owner_user_id: str
    source_type: TaskSourceType
    status: TaskIntakeStatus
    target: IntakeTarget | None = None
    task_spec: TaskSpec | None = None
    missing_fields: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    create_time_ms: int = 0
    update_time_ms: int = 0


def missing_intake_fields(spec: TaskSpec | None) -> list[str]:
    """Require explicit goal, deliverables, acceptances, and constraints.

    An absent ``constraints`` key means the user has not answered; an explicit
    empty list means there are no constraints and is complete.
    """
    if spec is None:
        return ["objective", "deliverables", "acceptances", "constraints"]
    props = spec.context.extend_props
    missing: list[str] = []
    if not spec.goal.objective.strip():
        missing.append("objective")
    if not isinstance(props.get("deliverables"), list) or not any(
        str(item).strip() for item in props["deliverables"]
    ):
        missing.append("deliverables")
    if not spec.goal.acceptances or any(
        not item.description.strip() for item in spec.goal.acceptances
    ):
        missing.append("acceptances")
    if "constraints" not in props or not isinstance(props["constraints"], list):
        missing.append("constraints")
    return missing
