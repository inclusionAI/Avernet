"""
FusionConflictConclusion

G2: Conflict Alignment Layer

冲突结论领域模型定义，用于描述 G2 场景中冲突分析的综合结论。

该模型解决的问题：
- 用户不知道下一步怎么办
- 缺乏优先级和严重性判断
- 没有明确的推进建议
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ConflictConclusion(BaseModel):
    """
    冲突结论

    G2 场景中冲突分析的综合结论，为用户提供明确的决策支持。

    设计原则：
    1. 可执行性 - 提供明确的行动建议
    2. 优先级 - 区分轻重缓急
    3. 可解释性 - 说明判断依据
    4. 完整性 - 覆盖所有关键维度

    Attributes:
        overall_severity: 整体冲突严重程度 (low/medium/high/critical)
        resolution_strategy: 解决策略建议
        go_no_go: 是否推进的建议 (go/no_go/conditional_go/need_discussion)
        priority_actions: 优先行动项列表（按优先级排序）
        reasoning: 推理过程说明
        risks: 关键风险列表
        conditions: 推进条件（当 go_no_go 为 conditional_go 时）
    """

    model_config = {"extra": "forbid"}

    overall_severity: Literal["low", "medium", "high", "critical"] = Field(
        description="整体冲突严重程度",
    )

    resolution_strategy: str = Field(
        min_length=10,
        max_length=1000,
        description="解决策略建议",
    )

    go_no_go: Literal["go", "no_go", "conditional_go", "need_discussion"] = Field(
        description="是否推进的建议",
    )

    priority_actions: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="优先行动项列表（按优先级排序）",
    )

    reasoning: str = Field(
        min_length=10,
        max_length=2000,
        description="推理过程说明",
    )

    risks: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="关键风险列表",
    )

    conditions: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="推进条件（当 go_no_go 为 conditional_go 时）",
    )


__all__ = [
    "ConflictConclusion",
]