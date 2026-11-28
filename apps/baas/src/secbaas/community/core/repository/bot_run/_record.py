"""Bot Run Record

表示一次 Bot 对话运行的完整状态。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class BcnCallbackState(Enum):
    """Durable delivery state for the BCN final callback."""

    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    SENT = "SENT"
    TERMINAL_FAILED = "TERMINAL_FAILED"


class RunStatus(Enum):
    """运行状态枚举"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIME_OUT = "TIME_OUT"
    ABORTED = "ABORTED"


TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.TIME_OUT.value,
        RunStatus.ABORTED.value,
    }
)


@dataclass(slots=True)
class BcnCallbackClaim:
    """A fenced callback lease claimed from durable run state."""

    record: "BotRunRecord"
    lease_token: str
    lease_generation: int


@dataclass(slots=True)
class BotRunRecord:
    """Bot Run 记录

    表示一次 Bot 对话运行的完整状态。
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    run_id: str
    bot_id: str
    api_key_prefix: str
    message: str
    message_long: str | None
    metadata: dict[str, Any] | None
    status: str
    result_content: str | None
    result_content_long: str | None
    result_extra: dict[str, Any] | None
    error: str | None
    completed_at: datetime | None
    bcn_callback_function: str | None = None
    bcn_callback_eligible: bool = False
    bcn_callback_state: str | None = None
    bcn_callback_sent: bool = False
    bcn_callback_attempts: int = 0
    bcn_callback_next_retry_at: datetime | None = None
    bcn_callback_in_flight_until: datetime | None = None
    bcn_callback_lease_token: str | None = None
    bcn_callback_lease_generation: int = 0
    bcn_callback_last_status: str | None = None
    bcn_callback_last_error_kind: str | None = None
