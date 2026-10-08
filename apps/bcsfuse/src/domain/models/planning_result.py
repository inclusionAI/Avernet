"""
Planning Result Model

M4: Research & Planning Engine

规划结果模型，封装 Planner 的输出结构。

关键设计决策：
- plan_draft：与 schemas/PlanDraft.json 严格对齐
- 扩展字段（objective, dependencies, risks, assumptions, open_questions, fallbacks, status, confidence）
  放在 PlanningResult 中，不放入 PlanDraft，以保持与 Schema 的严格一致

遵循 CLAUDE.md 的约束：
- 领域模型不依赖具体实现
- 对象稳定、边界清晰
- 不擅自修改契约

扩展字段说明：
- objective：计划目标（从 TaskSpec.goal 派生）
- dependencies：步骤间/任务间依赖关系
- risks：识别的风险列表
- assumptions：规划假设
- open_questions：待确认问题
- fallbacks：备选方案
- status：计划状态（draft/ready/blocked）
- confidence：置信度分数（0.0-1.0）
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.plan_draft import PlanDraft


class PlanningWarning(BaseModel):
    """
    规划警告

    表示规划过程中发现的非阻塞性问题。
    """

    field: str = Field(..., description="相关字段")
    message: str = Field(..., description="警告消息")
    suggestion: Optional[str] = Field(None, description="改进建议")

    model_config = {
        "extra": "forbid",
    }


class PlanningError(BaseModel):
    """
    规划错误

    表示规划过程中发现的阻塞性问题。
    """

    field: str = Field(..., description="相关字段")
    message: str = Field(..., description="错误消息")
    severity: str = Field(default="medium", description="严重程度")

    model_config = {
        "extra": "forbid",
    }


class PlanRisk(BaseModel):
    """
    计划风险

    表示规划过程中识别的风险项。
    """

    risk_id: str = Field(..., description="风险 ID")
    description: str = Field(..., description="风险描述")
    severity: str = Field(default="medium", description="风险严重程度 (low/medium/high/critical)")
    mitigation: Optional[str] = Field(None, description="缓解措施")

    model_config = {
        "extra": "forbid",
    }


class PlanFallback(BaseModel):
    """
    计划备选方案

    表示当主方案失败时的备选方案。
    """

    fallback_id: str = Field(..., description="备选方案 ID")
    trigger: str = Field(..., description="触发条件")
    action: str = Field(..., description="备选动作")

    model_config = {
        "extra": "forbid",
    }


class DependencyRef(BaseModel):
    """
    依赖关系引用

    表示步骤间或任务间的依赖关系。
    """

    from_step: str = Field(..., description="依赖方步骤 ID")
    to_step: str = Field(..., description="被依赖步骤 ID")
    dependency_type: str = Field(
        default="sequential",
        description="依赖类型 (sequential/parallel/data/resource)",
    )

    model_config = {
        "extra": "forbid",
    }


class PlanningResult(BaseModel):
    """
    规划结果

    封装 Planning Engine 的输出。

    设计原则：
    - plan_draft 与 schemas/PlanDraft.json 严格对齐
    - 扩展字段放在本层，不修改 PlanDraft 契约
    - 支持部分成功：有 warnings 但无 errors 仍算成功
    """

    # 核心输出：与 Schema 对齐的 PlanDraft
    plan_draft: PlanDraft = Field(..., description="计划草案（与 Schema 对齐）")

    # 扩展字段：承载 Schema 中未定义但规划需要的字段
    objective: str = Field(..., description="计划目标")
    dependencies: list[DependencyRef] = Field(
        default_factory=list,
        description="依赖关系列表",
    )
    risks: list[PlanRisk] = Field(
        default_factory=list,
        description="风险列表",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="假设列表",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="待确认问题列表",
    )
    fallbacks: list[PlanFallback] = Field(
        default_factory=list,
        description="备选方案列表",
    )

    # 状态相关
    status: str = Field(
        default="draft",
        description="计划状态 (draft/ready/blocked)",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="置信度 (0.0-1.0)",
    )

    # 警告和错误
    warnings: list[PlanningWarning] = Field(
        default_factory=list,
        description="规划警告列表",
    )
    errors: list[PlanningError] = Field(
        default_factory=list,
        description="规划错误列表",
    )

    model_config = {
        "extra": "forbid",
    }

    def is_successful(self) -> bool:
        """
        判断规划是否成功

        成功条件：
        - 有有效的 PlanDraft
        - 没有 errors

        Returns:
            bool: 是否成功
        """
        return self.plan_draft is not None and len(self.errors) == 0

    def get_summary(self) -> dict:
        """
        获取结果摘要

        Returns:
            dict: 结果摘要
        """
        return {
            "task_id": self.plan_draft.task_id if self.plan_draft else None,
            "objective": self.objective,
            "status": self.status,
            "confidence": self.confidence,
            "steps_count": len(self.plan_draft.steps) if self.plan_draft else 0,
            "risks_count": len(self.risks),
            "open_questions_count": len(self.open_questions),
            "warnings_count": len(self.warnings),
            "errors_count": len(self.errors),
            "is_successful": self.is_successful(),
        }


__all__ = [
    "PlanningResult",
    "PlanningWarning",
    "PlanningError",
    "PlanRisk",
    "PlanFallback",
    "DependencyRef",
]