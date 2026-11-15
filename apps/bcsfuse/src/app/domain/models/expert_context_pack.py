"""
ExpertContextPack

Stage 3: Worker Profile-Driven Expert Execution Preparation

G5 LLM 输入上下文模型，面向 LLM 的最终输入包。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ExpertContextPack(BaseModel):
    """
    G5 LLM 输入上下文

    面向 LLM 的最终输入包，由 WorkerContextDigest 聚合而成。
    用于 G5 专家视角生成。

    Attributes:
        question: 待诊断的问题
        expert_id: 专家标识 (格式: worker_id:default)
        profile_key: Profile 唯一键，用于 traceability/debugging/source tracking
        domain: 推断的领域 (security/legal/database/ops/tech/architecture)
        expertise_summary: 专长摘要（来自 digest 汇总）
        relevant_skills: 相关技能名称列表
        context_highlights: 上下文要点（来自 fragments 汇总）
        task_context: 任务上下文描述
    """

    model_config = {"extra": "forbid"}

    question: str = Field(
        min_length=1,
        max_length=2000,
        description="待诊断的问题",
    )

    expert_id: str = Field(
        min_length=1,
        max_length=256,
        description="专家标识 (格式: worker_id:default)",
    )

    profile_key: str = Field(
        min_length=1,
        max_length=512,
        description="Profile 唯一键，用于 traceability/debugging/source tracking",
    )

    domain: str = Field(
        min_length=1,
        max_length=64,
        description="推断的领域 (security/legal/database/ops/tech/architecture)",
    )

    expertise_summary: str = Field(
        min_length=1,
        max_length=2000,
        description="专长摘要（来自 digest 汇总）",
    )

    relevant_skills: list[str] = Field(
        default_factory=list,
        description="相关技能名称列表",
    )

    context_highlights: list[str] = Field(
        default_factory=list,
        description="上下文要点（来自 fragments 汇总）",
    )

    task_context: str = Field(
        default="",
        max_length=2000,
        description="任务上下文描述",
    )


__all__ = [
    "ExpertContextPack",
]