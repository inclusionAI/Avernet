"""Worker profile 创建事件。"""

from __future__ import annotations

from pydantic import BaseModel


class WorkerProfileCreatedEvent(BaseModel):
    """Worker profile 创建/更新后触发，仅携带 worker_id 关联键。"""

    worker_id: str