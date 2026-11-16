"""06-evolution-run 的枚举（§2.4–§2.6、§11.1）。"""

from __future__ import annotations

from enum import IntEnum, StrEnum

__all__ = [
    "ChargeSource",
    "EndReason",
    "EventName",
    "OperationKind",
    "OperationStatus",
    "RiskCeiling",
    "RunStatus",
    "TriggerKind",
]


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"

    @property
    def terminal(self) -> bool:
        return self in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.BUDGET_EXHAUSTED,
        )


class EndReason(StrEnum):
    WORKER_FAILED = "worker_failed"
    MAX_ATTEMPTS = "max_attempts"
    CANCELLED_BY_OWNER = "cancelled_by_owner"
    KILL_SWITCH = "kill_switch"
    CONSECUTIVE_REJECTIONS = "consecutive_rejections"
    OUT_OF_BAND_CHANGE = "out_of_band_change"


class TriggerKind(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    EVENT = "event"


class EventName(StrEnum):
    FAILURE_RATE_ALERT = "failure_rate_alert"
    CLAWINSIGHT_IMPROVEMENT_ITEM = "clawinsight_improvement_item"
    FEEDBACK_THRESHOLD_REACHED = "feedback_threshold_reached"


class OperationKind(StrEnum):
    AGENT_SESSION = "agent_session"
    TRAIN_EVALUATION = "train_evaluation"


class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (
            OperationStatus.SUCCEEDED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
        )


class ChargeSource(StrEnum):
    MODEL_CALL = "model_call"
    OPERATION = "operation"
    EXPLICIT = "explicit"


class RiskCeiling(IntEnum):
    """`auto_promote_ceiling`（08 §5）：T3 永远不自动晋升，故不在此枚举。"""

    T0 = 0
    T1 = 1
    T2 = 2

    @classmethod
    def parse(cls, value: str | int | None) -> RiskCeiling | None:
        if value is None:
            return None
        if isinstance(value, int):
            return cls(value)
        return cls[str(value)]
