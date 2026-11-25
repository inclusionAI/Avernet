"""
Scoring Signal

Worker Profile Retrieval & Fusion Simulation Baseline

打分信号模型，用于表示检索匹配中的单个打分信号。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class SignalType:
    """预定义的信号类型常量"""

    CONTEXT_MATCH = "context_match"
    SKILL_NAME_MATCH = "skill_name_match"
    SKILL_DESC_MATCH = "skill_desc_match"
    SEARCHABLE_MATCH = "searchable_match"
    COVERAGE_SCORE = "coverage_score"
    PROFILE_TYPE_BONUS = "profile_type_bonus"
    DOMAIN_COVERAGE = "domain_coverage"
    MODE_BONUS = "mode_bonus"


class ScoringSignal(BaseModel):
    """
    打分信号模型

    表示检索匹配中的单个打分信号。

    Attributes:
        signal_type: 信号类型
        raw_score: 原始分数（0-1）
        weight: 权重（0-1）
        weighted_score: 加权分数（默认为 raw_score * weight）
        details: 详细信息
    """

    signal_type: str = Field(..., min_length=1, description="信号类型")
    raw_score: float = Field(..., ge=0, le=1, description="原始分数")
    weight: float = Field(..., ge=0, le=1, description="权重")
    weighted_score: Optional[float] = Field(None, ge=0, le=1, description="加权分数")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="详细信息"
    )

    @model_validator(mode="after")
    def calculate_weighted_score(self) -> "ScoringSignal":
        """计算加权分数"""
        if self.weighted_score is None:
            self.weighted_score = self.raw_score * self.weight
        return self

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "SignalType",
    "ScoringSignal",
]