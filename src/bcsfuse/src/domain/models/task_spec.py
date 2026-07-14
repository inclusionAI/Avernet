"""
TaskSpec Domain Model

任务规格模型，与 schemas/TaskSpec.json 对齐。

M0 骨架实现，M3 会完善。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    """风险等级枚举"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Subtask(BaseModel):
    """子任务"""
    id: str = Field(..., description="子任务 ID")
    title: str = Field(..., description="子任务标题")
    objective: str = Field(..., description="子任务目标")
    dependencies: list[str] = Field(default_factory=list, description="依赖的子任务 ID")


class TaskSpec(BaseModel):
    """
    任务规格模型

    对应 JSON Schema: schemas/TaskSpec.json
    """
    id: str = Field(
        ...,
        pattern=r"^tsk_[a-zA-Z0-9_-]+$",
        description="任务唯一标识"
    )
    goal: str = Field(..., min_length=1, description="任务目标")
    deliverables: list[str] = Field(..., min_length=1, description="交付物列表")
    constraints: list[str] = Field(default_factory=list, description="约束条件")
    success_criteria: list[str] = Field(..., min_length=1, description="成功标准")
    required_capabilities: list[str] = Field(..., min_length=1, description="所需能力")
    required_knowledge: list[str] = Field(default_factory=list, description="所需知识")
    required_resources: list[str] = Field(default_factory=list, description="所需资源")
    risk_level: RiskLevel = Field(..., description="风险等级")
    unknowns: list[str] = Field(default_factory=list, description="未知项")
    subtasks: list[Subtask] = Field(default_factory=list, description="子任务列表")
    source_prompt: Optional[str] = Field(None, description="原始用户输入")
    metadata: dict = Field(default_factory=dict, description="元数据")

    model_config = {
        "extra": "forbid",
        "use_enum_values": True,
    }


__all__ = [
    "TaskSpec",
    "RiskLevel",
    "Subtask",
]