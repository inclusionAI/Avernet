"""
Workspace Domain Model

工作空间模型，与 schemas/Workspace.json 对齐。

M0 骨架实现，M7 会完善。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from src.domain.models.team_spec import TeamSpec


class WorkspaceStatus(str, Enum):
    """工作空间状态枚举"""
    DRAFT = "draft"
    ASSEMBLED = "assembled"
    HANDED_OFF = "handed_off"
    CLOSED = "closed"


class WorkspaceEvent(BaseModel):
    """工作空间事件"""
    type: str = Field(..., description="事件类型")
    at: datetime = Field(..., description="事件时间")
    payload: dict[str, Any] = Field(default_factory=dict, description="事件载荷")

    model_config = {
        "extra": "forbid",
    }


class Workspace(BaseModel):
    """
    工作空间模型

    对应 JSON Schema: schemas/Workspace.json
    """
    id: str = Field(
        ...,
        pattern=r"^wsp_[a-zA-Z0-9_-]+$",
        description="工作空间唯一标识"
    )
    task_id: str = Field(..., description="关联任务 ID")
    team_spec: TeamSpec = Field(..., description="团队规格")
    knowledge_mounts: list[str] = Field(default_factory=list, description="知识挂载点")
    resource_mounts: list[str] = Field(default_factory=list, description="资源挂载点")
    artifacts: list[str] = Field(default_factory=list, description="工件列表")
    events: list[WorkspaceEvent] = Field(default_factory=list, description="事件日志")
    status: WorkspaceStatus = Field(..., description="状态")

    model_config = {
        "extra": "forbid",
        "use_enum_values": True,
    }


__all__ = [
    "Workspace",
    "WorkspaceStatus",
    "WorkspaceEvent",
]