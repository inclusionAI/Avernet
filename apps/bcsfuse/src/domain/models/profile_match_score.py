"""
ProfileMatchScore

Phase C: G1 Semantic Rerank V2

V2 Profile 匹配评分模型，用于表示两阶段评分的完整信息。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# =============================================================================
# 评分权重常量
# =============================================================================

# Phase 1: Base Score 权重
SEMANTIC_SIMILARITY_WEIGHT = 0.44
CAPABILITY_COVERAGE_WEIGHT = 0.28
SCENARIO_MATCH_WEIGHT = 0.18
AVAILABILITY_SCORE_WEIGHT = 0.10


class ScoreComponent(BaseModel):
    """
    单个评分组件

    表示评分公式中的单个组件及其详细信息。

    Attributes:
        raw_score: 原始分数 (0-1)
        weight: 权重 (0-1)
        weighted_score: 加权分数 (raw_score * weight)
        details: 详细信息字典，用于调试和分析
    """

    raw_score: float = Field(
        ge=0.0,
        le=1.0,
        description="原始分数 (0-1)",
    )

    weight: float = Field(
        ge=0.0,
        le=1.0,
        description="权重 (0-1)",
    )

    weighted_score: float = Field(
        ge=0.0,
        le=1.0,
        description="加权分数 (raw_score * weight)",
    )

    details: dict[str, Any] = Field(
        default_factory=dict,
        description="详细信息字典",
    )

    model_config = {
        "extra": "forbid",
    }


class ProfileMatchScore(BaseModel):
    """
    V2 Profile 匹配评分

    表示完整的两阶段评分结果：
    - Phase 1: Base Score 计算（单候选评分）
    - Phase 2: Diversity-Aware Rerank（结果集重排）

    Base Score 公式：
        base_score = semantic_similarity * 0.44
                   + capability_coverage * 0.28
                   + scenario_match * 0.18
                   + availability_score * 0.10

    Phase 2 通过 diversity rerank 调整最终分数。

    Attributes:
        final_score: 最终分数（两阶段后）
        semantic_similarity: 语义相似度组件
        capability_coverage: 能力覆盖度组件
        scenario_match: 场景匹配度组件
        availability_score: 可用性评分组件
        diversity_adjusted: 是否经过多样性调整
        diversity_delta: 多样性调整增量
        scorer_version: 评分器版本
        flags_enabled: 启用的 flags 列表
    """

    # =========================================================================
    # 最终分数
    # =========================================================================

    final_score: float = Field(
        ge=0.0,
        le=1.0,
        description="最终分数（两阶段后）",
    )

    # =========================================================================
    # Phase 1: Base Score 组件
    # =========================================================================

    semantic_similarity: Optional[ScoreComponent] = Field(
        default=None,
        description="语义相似度组件（taxonomy expansion + 文本匹配）",
    )

    capability_coverage: Optional[ScoreComponent] = Field(
        default=None,
        description="能力覆盖度组件（profile capabilities 覆盖程度）",
    )

    scenario_match: Optional[ScoreComponent] = Field(
        default=None,
        description="场景匹配度组件（taxonomy scenarios 匹配度）",
    )

    availability_score: Optional[ScoreComponent] = Field(
        default=None,
        description="可用性评分组件（worker registry 状态）",
    )

    # =========================================================================
    # Phase 2: Diversity Rerank 信息
    # =========================================================================

    diversity_adjusted: bool = Field(
        default=False,
        description="是否经过多样性调整",
    )

    diversity_delta: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="多样性调整增量（可为负值）",
    )

    # =========================================================================
    # 元数据
    # =========================================================================

    scorer_version: str = Field(
        default="v2",
        description="评分器版本",
    )

    flags_enabled: list[str] = Field(
        default_factory=list,
        description="启用的 flags 列表",
    )

    model_config = {
        "extra": "forbid",
    }

    # =========================================================================
    # 计算属性
    # =========================================================================

    @property
    def base_score(self) -> float:
        """
        计算 Base Score（Phase 1 结果）

        Returns:
            float: Base Score，如果组件缺失则返回 0
        """
        total = 0.0
        for component in [
            self.semantic_similarity,
            self.capability_coverage,
            self.scenario_match,
            self.availability_score,
        ]:
            if component is not None:
                total += component.weighted_score
        return min(1.0, max(0.0, total))

    @property
    def has_all_components(self) -> bool:
        """
        检查是否所有组件都已计算

        Returns:
            bool: 所有四个组件都存在
        """
        return all([
            self.semantic_similarity is not None,
            self.capability_coverage is not None,
            self.scenario_match is not None,
            self.availability_score is not None,
        ])

    @property
    def component_count(self) -> int:
        """
        已计算的组件数量

        Returns:
            int: 已计算的组件数量（0-4）
        """
        return sum([
            self.semantic_similarity is not None,
            self.capability_coverage is not None,
            self.scenario_match is not None,
            self.availability_score is not None,
        ])

    # =========================================================================
    # 工厂方法
    # =========================================================================

    @classmethod
    def create_empty(cls) -> ProfileMatchScore:
        """
        创建空的评分结果

        Returns:
            ProfileMatchScore: 空评分结果（final_score=0）
        """
        return cls(final_score=0.0)

    @classmethod
    def create_from_base_score(
        cls,
        semantic_similarity: float,
        capability_coverage: float,
        scenario_match: float,
        availability_score: float,
        semantic_details: Optional[dict[str, Any]] = None,
        capability_details: Optional[dict[str, Any]] = None,
        scenario_details: Optional[dict[str, Any]] = None,
        availability_details: Optional[dict[str, Any]] = None,
        flags_enabled: Optional[list[str]] = None,
    ) -> ProfileMatchScore:
        """
        从 Base Score 组件创建评分结果

        Args:
            semantic_similarity: 语义相似度原始分
            capability_coverage: 能力覆盖度原始分
            scenario_match: 场景匹配度原始分
            availability_score: 可用性评分原始分
            semantic_details: 语义相似度详细信息
            capability_details: 能力覆盖度详细信息
            scenario_details: 场景匹配度详细信息
            availability_details: 可用性评分详细信息
            flags_enabled: 启用的 flags 列表

        Returns:
            ProfileMatchScore: 完整的评分结果
        """
        # 构建组件
        sem_component = ScoreComponent(
            raw_score=semantic_similarity,
            weight=SEMANTIC_SIMILARITY_WEIGHT,
            weighted_score=semantic_similarity * SEMANTIC_SIMILARITY_WEIGHT,
            details=semantic_details or {},
        )

        cap_component = ScoreComponent(
            raw_score=capability_coverage,
            weight=CAPABILITY_COVERAGE_WEIGHT,
            weighted_score=capability_coverage * CAPABILITY_COVERAGE_WEIGHT,
            details=capability_details or {},
        )

        sce_component = ScoreComponent(
            raw_score=scenario_match,
            weight=SCENARIO_MATCH_WEIGHT,
            weighted_score=scenario_match * SCENARIO_MATCH_WEIGHT,
            details=scenario_details or {},
        )

        ava_component = ScoreComponent(
            raw_score=availability_score,
            weight=AVAILABILITY_SCORE_WEIGHT,
            weighted_score=availability_score * AVAILABILITY_SCORE_WEIGHT,
            details=availability_details or {},
        )

        # 计算 base_score
        base_score = (
            sem_component.weighted_score
            + cap_component.weighted_score
            + sce_component.weighted_score
            + ava_component.weighted_score
        )

        return cls(
            final_score=min(1.0, max(0.0, base_score)),
            semantic_similarity=sem_component,
            capability_coverage=cap_component,
            scenario_match=sce_component,
            availability_score=ava_component,
            flags_enabled=flags_enabled or [],
        )


__all__ = [
    "ScoreComponent",
    "ProfileMatchScore",
    "SEMANTIC_SIMILARITY_WEIGHT",
    "CAPABILITY_COVERAGE_WEIGHT",
    "SCENARIO_MATCH_WEIGHT",
    "AVAILABILITY_SCORE_WEIGHT",
]