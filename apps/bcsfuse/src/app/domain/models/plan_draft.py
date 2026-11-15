"""
PlanDraft Domain Model

计划草案模型，与 schemas/PlanDraft.json 对齐。

M0 骨架实现，M4 会完善。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    """计划步骤"""
    id: str = Field(..., description="步骤 ID")
    title: str = Field(..., description="步骤标题")
    objective: str = Field(..., description="步骤目标")
    required_capabilities: list[str] = Field(default_factory=list, description="所需能力")
    risk_notes: list[str] = Field(default_factory=list, description="风险说明")


class PlanDraft(BaseModel):
    """
    计划草案模型

    对应 JSON Schema: schemas/PlanDraft.json
    """
    task_id: str = Field(..., description="关联任务 ID")
    strategy: str = Field(..., description="策略摘要")
    steps: list[PlanStep] = Field(..., min_length=1, description="计划步骤")
    role_requirements: list[str] = Field(default_factory=list, description="角色需求")
    knowledge_requirements: list[str] = Field(default_factory=list, description="知识需求")
    resource_requirements: list[str] = Field(default_factory=list, description="资源需求")
    handoff_strategy: str = Field(..., description="交接策略")
    escalation_points: list[str] = Field(default_factory=list, description="升级点")

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "PlanDraft",
    "PlanStep",
]