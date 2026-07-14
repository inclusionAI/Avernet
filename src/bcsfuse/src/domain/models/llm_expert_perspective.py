"""
LLMExpertPerspective

Stage 3: Worker Profile-Driven Expert Execution Preparation

LLM 生成的专家视角模型，用于 G5 模式。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Risk level literal type for type hints
RiskLevelLiteral = Literal["low", "medium", "high", "critical"]


class LLMExpertPerspective(BaseModel):
    """
    LLM 生成的专家视角

    用于 G5 Expert Diagnosis 模式，由 LLM 根据专家 profile 生成。

    注意：不包含 reasoning 字段，使用更可控的表述。

    Attributes:
        summary: 视角摘要
        confidence: 置信度 (0-1)
        key_points: 核心观点列表
        concerns: 主要顾虑列表
        risk_level: 风险等级 (low/medium/high/critical)
        rationale_summary: 依据摘要（避免 "reasoning" 表述）
        evidence_summary: 证据摘要列表
    """

    model_config = {"extra": "forbid"}

    summary: str = Field(
        min_length=1,
        max_length=2000,
        description="视角摘要",
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="置信度 (0-1)",
    )

    key_points: list[str] = Field(
        default_factory=list,
        description="核心观点列表",
    )

    concerns: list[str] = Field(
        default_factory=list,
        description="主要顾虑列表",
    )

    risk_level: RiskLevelLiteral = Field(
        description="风险等级 (low/medium/high/critical)",
    )

    rationale_summary: str = Field(
        min_length=1,
        max_length=2000,
        description="依据摘要（避免 'reasoning' 表述）",
    )

    evidence_summary: list[str] = Field(
        default_factory=list,
        description="证据摘要列表",
    )


__all__ = [
    "LLMExpertPerspective",
    "RiskLevelLiteral",
]