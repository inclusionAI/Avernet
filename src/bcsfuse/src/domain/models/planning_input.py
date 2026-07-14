"""
Planning Input Model

M4: Research & Planning Engine

规划输入模型，封装 Planner 的输入结构。

输入侧包含：
- task_spec：TaskSpec（来自 M3，必需）
- understanding_warnings：理解警告列表（可选）
- understanding_errors：理解错误列表（可选）
- source_prompt：原始用户输入（可选）
- planning_hints：规划提示（可选）

遵循 CLAUDE.md 的约束：
- 领域模型不依赖具体实现
- 对象稳定、边界清晰
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.task_spec import TaskSpec
from src.domain.models.task_understanding_result import (
    UnderstandingWarning,
    UnderstandingError,
)


class PlanningInput(BaseModel):
    """
    规划输入模型

    封装 Planning Engine 的输入，包含 TaskSpec 及相关上下文。
    """

    task_spec: TaskSpec = Field(..., description="任务规格（来自 Task Understanding）")
    understanding_warnings: list[UnderstandingWarning] = Field(
        default_factory=list,
        description="来自 Task Understanding 的警告",
    )
    understanding_errors: list[UnderstandingError] = Field(
        default_factory=list,
        description="来自 Task Understanding 的错误",
    )
    source_prompt: Optional[str] = Field(
        None,
        description="原始用户输入",
    )
    planning_hints: dict = Field(
        default_factory=dict,
        description="规划提示",
    )

    model_config = {
        "extra": "forbid",
    }


__all__ = ["PlanningInput"]