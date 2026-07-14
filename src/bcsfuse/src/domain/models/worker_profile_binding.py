"""
Worker Profile Binding

Worker 与 Profile 的绑定关系模型。

Stage 1 只支持：一个 Worker 只有一个 active profile。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.worker_source_info import WorkerSourceType


class WorkerProfileBinding(BaseModel):
    """
    Worker 与 Profile 的绑定关系

    Stage 1 规则：
    - 一个 Worker 只能有一个 active profile
    - 可以预留多 profile 结构，但不实现复杂切换

    Attributes:
        id: 绑定记录 ID
        worker_id: Worker ID
        profile_key: Profile 唯一标识（格式：worker_id:profile_id）
        is_active: 是否为活跃绑定（Stage 1 总是 True）
        source_type: 来源类型
        bound_at: 绑定时间
        unbound_at: 解绑时间（如果已解绑）
        updated_at: 更新时间
    """

    id: str = Field(
        default_factory=lambda: f"binding_{uuid.uuid4().hex[:12]}",
        description="绑定记录 ID"
    )
    worker_id: str = Field(
        ...,
        min_length=1,
        description="Worker ID"
    )
    profile_key: str = Field(
        ...,
        min_length=1,
        description="Profile 唯一标识"
    )
    is_active: bool = Field(
        default=True,
        description="是否为活跃绑定"
    )
    source_type: WorkerSourceType = Field(
        default=WorkerSourceType.API,
        description="来源类型"
    )
    bound_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="绑定时间"
    )
    unbound_at: Optional[datetime] = Field(
        None,
        description="解绑时间"
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="更新时间"
    )

    model_config = {
        "extra": "forbid",
    }


__all__ = ["WorkerProfileBinding"]