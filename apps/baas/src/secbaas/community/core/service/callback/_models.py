# Copyright (c) 2004-2026, Ant Group.
# All Rights Reserved.

"""HTTP Callback 数据模型"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class CallbackPayload:
    """回调请求体

    与 BotRunRecord 的关键字段对齐，调用方可据此判断执行结果。
    """

    run_id: str
    bot_id: str
    status: str
    result: str | None = None
    error: str | None = None
    metadata: dict[str, Any] | None = None
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "bot_id": self.bot_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
            "session_id": self.session_id,
        }


@dataclass(slots=True, frozen=True)
class CallbackResult:
    """回调发送结果"""

    success: bool
    status_code: int | None = None
    message: str = ""
