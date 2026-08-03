"""Bot Run Record

表示一次 Bot 对话运行的完整状态。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class RunStatus(Enum):
    """运行状态枚举"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIME_OUT = "TIME_OUT"
    ABORTED = "ABORTED"


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
