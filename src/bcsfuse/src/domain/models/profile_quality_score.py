"""
Profile Quality Score Model

Profile 质量评分与过滤系统 - 评分结果模型

简化设计：只保留核心的分数和问题列表。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# =============================================================================
# 质量阈值（用于判断是否可接受）
# =============================================================================

QUALITY_THRESHOLD_ACCEPTABLE = 0.30  # 可接受阈值


class ProfileQualityScore(BaseModel):
    """
    Profile 质量评分结果模型（精简版）

    Attributes:
        profile_key: Profile 标识
        total_score: 总分数（0-1）
        issues: 问题列表，带前缀区分类型
            - [WARN] 严重问题，影响分数
            - [SUGGEST] 改进建议，可优化但非阻塞
    """
    profile_key: str = Field(..., min_length=1, description="Profile 标识")
    total_score: float = Field(..., ge=0, le=1, description="总分数（0-1）")
    issues: list[str] = Field(default_factory=list, description="问题列表（带前缀区分类型）")

    @property
    def is_acceptable(self) -> bool:
        """是否可接受（>= 0.30）"""
        return self.total_score >= QUALITY_THRESHOLD_ACCEPTABLE


__all__ = [
    "QUALITY_THRESHOLD_ACCEPTABLE",
    "ProfileQualityScore",
]