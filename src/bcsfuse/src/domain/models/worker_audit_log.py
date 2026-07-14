"""
Worker Audit Log

Worker 审计日志模型。

Stage 1 最小审计能力：
- action
- worker_id
- actor
- source_type
- before/after 摘要
- timestamp
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.worker_source_info import WorkerSourceType


class WorkerAuditAction(str, Enum):
    """
    Worker 审计动作

    Stage 1 只记录关键操作：
    - CREATED: 创建 worker
    - UPDATED: 更新 worker 信息
    - IMPORTED: 从文件导入 worker
    - LIFECYCLE_CHANGED: 生命周期状态变化
    - RUNTIME_STATE_CHANGED: 运行态变化
    - AVAILABILITY_CHANGED: 可见性状态变化
    - CONFIG_CHANGED: 行为配置变化（如 fusion_enable）
    - DELETED: 删除 worker
    """
    CREATED = "created"
    UPDATED = "updated"
    IMPORTED = "imported"
    LIFECYCLE_CHANGED = "lifecycle_changed"
    RUNTIME_STATE_CHANGED = "runtime_state_changed"
    AVAILABILITY_CHANGED = "availability_changed"
    CONFIG_CHANGED = "config_changed"
    DELETED = "deleted"


class WorkerAuditLog(BaseModel):
    """
    Worker 审计日志

    Stage 1 最小字段，满足追溯需求。

    Attributes:
        id: 日志 ID
        worker_id: Worker ID
        action: 审计动作
        old_value: 变更前值（JSON 字符串）
        new_value: 变更后值（JSON 字符串）
        source_type: 来源类型
        source_ref: 来源引用
        performed_by: 执行者（actor）
        performed_at: 执行时间
    """

    id: str = Field(
        default_factory=lambda: f"audit_{uuid.uuid4().hex[:12]}",
        description="日志 ID"
    )
    worker_id: str = Field(
        ...,
        min_length=1,
        description="Worker ID"
    )
    action: WorkerAuditAction = Field(
        ...,
        description="审计动作"
    )
    old_value: Optional[str] = Field(
        None,
        description="变更前值（JSON 字符串）"
    )
    new_value: Optional[str] = Field(
        None,
        description="变更后值（JSON 字符串）"
    )
    source_type: WorkerSourceType = Field(
        default=WorkerSourceType.API,
        description="来源类型"
    )
    source_ref: Optional[str] = Field(
        None,
        description="来源引用"
    )
    performed_by: Optional[str] = Field(
        None,
        description="执行者"
    )
    performed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="执行时间"
    )

    model_config = {
        "extra": "forbid",
    }


__all__ = ["WorkerAuditAction", "WorkerAuditLog"]