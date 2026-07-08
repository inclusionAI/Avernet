"""Bot Run 队列工作项数据模型。

队列化改造把"请求结果记录"（``baas_bot_run``，持久、被 ``GET /runs`` 读取）
与"队列工作项"（``baas_bot_run_queue``，瞬态、高频 claim/heartbeat、可 TTL 清理）
拆开。本模块包含 ``BotRunQueueRecord``、``QueueStatus`` 与 ``_parse_meta_json``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class QueueStatus(Enum):
    """队列工作项状态。

    - PENDING：待认领。
    - RUNNING：已被某 Worker 认领、执行中。
    - DONE：执行已写入终态（结果落在 ``baas_bot_run``），等待 TTL 清理。
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"


def _parse_meta_json(raw: str | None) -> dict:
    """安全解析 meta JSON 字符串，失败返回空 dict。"""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


@dataclass(slots=True)
class BotRunQueueRecord:
    """Bot Run 队列工作项记录。"""

    id: int
    gmt_create: datetime | None
    gmt_modified: datetime | None
    run_id: str
    bot_id: str
    session_id: str | None
    status: str
    assigned_worker: str | None
    last_heartbeat: datetime | None
    meta: dict[str, Any] = field(default_factory=dict)
    env: str | None = None
