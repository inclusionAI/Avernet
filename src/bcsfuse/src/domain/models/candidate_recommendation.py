"""
CandidateRecommendation

Stage 4: G5 real-context deepening / candidate recommendation 正式接入

候选人推荐结果模型，用于 G5 专家诊断场景。

Phase C: G1 Semantic Rerank V2
- 新增可选字段 score_breakdown 用于输出 V2 评分明细
"""

from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import BaseModel, Field

from src.domain.models.domain_coverage import DomainCoverage
from src.domain.models.profile_match_score import ProfileMatchScore
from src.domain.models.retrieval_mode import RetrievalMode


class FragmentMatchInfo(BaseModel):
    """
    Fragment 匹配信息

    表示检索时某个 Fragment 的匹配得分详情，用于解释推荐理由。
    """
    fragment_type: str = Field(..., description="片段类型，如 soul, agents, tools 等")
    score: float = Field(..., description="原始相似度分数")
    weighted_score: float = Field(..., description="加权后的分数")
    content_preview: str = Field(default="", description="内容预览")

    model_config = {
        "extra": "forbid",
    }


class CandidateRecommendation(BaseModel):
    """
    候选人推荐结果

    表示单个候选人的推荐结果，包含推荐理由和匹配信息。

    Attributes:
        profile_key: Profile 唯一键
        worker_id: Worker ID (staff_id)
        score: 推荐分数 (0-1)
        reasons: 推荐理由列表
        domain: 推断的领域
        domain_confidence: 领域推断置信度 (0-1)
        matched_skills: 匹配的技能名称列表
        matched_contexts: 匹配的上下文来源列表
        is_supplement: 是否为补充推荐（非显式 participants）
    """

    # 标识
    profile_key: str = Field(
        min_length=1,
        description="Profile 唯一键",
    )

    worker_id: str = Field(
        min_length=1,
        description="Worker ID (staff_id)",
    )

    # 推荐分数
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="推荐分数 (0-1)",
    )

    # 推荐理由（支持字符串或结构化对象）
    reasons: list[Union[str, dict[str, Any]]] = Field(
        default_factory=list,
        description="推荐理由列表，支持纯文本字符串或结构化对象（包含 fragment scores 等）",
    )

    # 领域信息
    domain: str = Field(
        default="general",
        description="推断的领域",
    )

    domain_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="领域推断置信度 (0-1)",
    )

    # 匹配信息
    matched_skills: list[str] = Field(
        default_factory=list,
        description="匹配的技能名称列表",
    )

    matched_contexts: list[str] = Field(
        default_factory=list,
        description="匹配的上下文来源列表",
    )

    # 辅助标记
    is_supplement: bool = Field(
        default=False,
        description="是否为补充推荐（非显式 participants）",
    )

    # 精简画像（30字以内）
    short_profile: str = Field(
        default="",
        description="精简画像（30字以内），用于快速展示",
    )

    # 信任等级
    trust_level: str = Field(
        default="",
        description="信任等级（unverified/verifying/trusted/guarded/sandbox_only）",
    )

    # =========================================================================
    # Phase C: V2 评分明细（可选）
    # =========================================================================

    score_breakdown: Optional[ProfileMatchScore] = Field(
        default=None,
        description="V2 评分明细（可选，需 ENABLE_G1_SCORE_BREAKDOWN_OUTPUT=true）",
    )

    @property
    def is_high_confidence(self) -> bool:
        """
        是否高置信度推荐

        Returns:
            bool: 推荐分数 >= 0.7 且领域置信度 >= 0.5
        """
        return self.score >= 0.7 and self.domain_confidence >= 0.5

    @property
    def is_low_confidence(self) -> bool:
        """
        是否低置信度推荐

        Returns:
            bool: 推荐分数 < 0.5
        """
        return self.score < 0.5

    model_config = {
        "extra": "forbid",
    }


class CandidateRecommendationResponse(BaseModel):
    """
    候选人推荐响应

    表示完整的推荐响应，包含多个候选人和领域覆盖分析。

    Attributes:
        recommendations: 推荐结果列表（显式 participants 在前，补充推荐在后）
        question: 原始问题
        mode: 检索模式
        domain_coverage: 领域覆盖分析
        participants_given: 是否有显式 participants
        participants_sufficient: 显式 participants 是否充足
        total_candidates: 总候选人数
        selected_candidates: 选中的候选人数
        min_experts: 最小专家数阈值
    """

    # 推荐结果（顺序：显式 participants + 补充推荐）
    recommendations: list[CandidateRecommendation] = Field(
        default_factory=list,
        description="推荐结果列表",
    )

    # 基本信息
    question: str = Field(
        min_length=1,
        description="原始问题",
    )

    mode: RetrievalMode = Field(
        description="检索模式",
    )

    # 领域覆盖分析
    domain_coverage: DomainCoverage = Field(
        default_factory=DomainCoverage,
        description="领域覆盖分析",
    )

    # Participants 状态
    participants_given: bool = Field(
        default=False,
        description="是否有显式 participants",
    )

    participants_sufficient: bool = Field(
        default=False,
        description="显式 participants 是否充足",
    )

    # 统计信息
    total_candidates: int = Field(
        default=0,
        ge=0,
        description="总候选人数",
    )

    selected_candidates: int = Field(
        default=0,
        ge=0,
        description="选中的候选人数",
    )

    min_experts: int = Field(
        default=3,
        ge=1,
        description="最小专家数阈值",
    )

    # Phase C: 诊断 metadata（用于调试和监控）
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="诊断 metadata，用于调试和监控推荐过程",
    )

    @property
    def explicit_participants(self) -> list[CandidateRecommendation]:
        """
        获取显式 participants

        Returns:
            list[CandidateRecommendation]: is_supplement=False 的推荐
        """
        return [r for r in self.recommendations if not r.is_supplement]

    @property
    def supplement_candidates(self) -> list[CandidateRecommendation]:
        """
        获取补充推荐

        Returns:
            list[CandidateRecommendation]: is_supplement=True 的推荐
        """
        return [r for r in self.recommendations if r.is_supplement]

    @property
    def needs_more_candidates(self) -> bool:
        """
        是否需要更多候选人

        Returns:
            bool: 选中候选人数 < 最小专家数
        """
        return self.selected_candidates < self.min_experts

    @property
    def high_confidence_count(self) -> int:
        """
        高置信度推荐数量

        Returns:
            int: is_high_confidence=True 的推荐数量
        """
        return sum(1 for r in self.recommendations if r.is_high_confidence)

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "FragmentMatchInfo",
    "CandidateRecommendation",
    "CandidateRecommendationResponse",
]