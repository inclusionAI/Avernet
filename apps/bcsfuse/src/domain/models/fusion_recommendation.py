"""
FusionRecommendation

LLM Gateway / Provider Layer

融合建议输出模型，描述 G1 recommendation 的结构化输出。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Decision(str, Enum):
    """
    决策枚举

    描述融合建议的最终决策。
    """

    YES = "yes"
    NO = "no"
    CONDITIONAL_YES = "conditional_yes"
    NEEDS_MORE_INFORMATION = "needs_more_information"


class FusionRecommendation(BaseModel):
    """
    融合建议

    G1 Fusion Recommendation 的结构化输出模型。

    Attributes:
        summary: 建议摘要，面向用户可展示
        decision: 最终决策（yes/no/conditional_yes/needs_more_information）
        reasoning: 推理过程摘要，可展示的理由列表
        risks: 风险列表
        missing_information: 缺失信息列表
        next_actions: 下一步行动建议
        confidence: 置信度（0-1）
    """

    model_config = {"extra": "forbid"}

    summary: str = Field(
        min_length=1,
        max_length=2000,
        description="建议摘要，面向用户可展示",
    )

    decision: Decision = Field(
        description="最终决策",
    )

    reasoning: list[str] = Field(
        default_factory=list,
        description="推理过程摘要，可展示的理由列表",
    )

    risks: list[str] = Field(
        default_factory=list,
        description="风险列表",
    )

    missing_information: list[str] = Field(
        default_factory=list,
        description="缺失信息列表",
    )

    next_actions: list[str] = Field(
        default_factory=list,
        description="下一步行动建议",
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="置信度（0-1）",
    )


__all__ = [
    "FusionRecommendation",
    "Decision",
]