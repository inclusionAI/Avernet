"""
StructuredConflictAnalysis - 结构化冲突分析模型

G2 Conflict Alignment Layer - Phase B

描述 G2 场景中多方视角的结构化冲突分析结果。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.domain.models.stance_signal import StanceSignal


class PairwiseConflict(BaseModel):
    """
    两两冲突/对齐分析

    描述两个参与者之间的冲突或对齐关系。

    Attributes:
        participant_a: 参与者 A 的 ID
        participant_b: 参与者 B 的 ID
        dimension_id: 冲突维度 ID
        conflict_type: 关系类型
            - conflict: 明确冲突（立场对立）
            - alignment: 立场一致
            - tension: 存在张力（一方强，一方弱/平衡）
            - none: 无明显关系
        stance_a: 参与者 A 的立场信号
        stance_b: 参与者 B 的立场信号
        severity: 冲突严重程度（仅 conflict 类型有意义）
        confidence: 判定置信度
        evidence: 支持判定的证据
        rationale: 判定理由
    """

    model_config = {"extra": "forbid"}

    participant_a: str = Field(
        description="参与者 A 的 ID",
    )
    participant_b: str = Field(
        description="参与者 B 的 ID",
    )
    dimension_id: str = Field(
        description="冲突维度 ID",
    )
    conflict_type: Literal["conflict", "alignment", "tension", "none"] = Field(
        description="关系类型",
    )
    stance_a: StanceSignal = Field(
        description="参与者 A 的立场信号",
    )
    stance_b: StanceSignal = Field(
        description="参与者 B 的立场信号",
    )
    severity: Optional[Literal["low", "medium", "high", "critical"]] = Field(
        default=None,
        description="冲突严重程度",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="判定置信度",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="支持判定的证据",
    )
    rationale: Optional[str] = Field(
        default=None,
        max_length=500,
        description="判定理由",
    )


class DimensionSummary(BaseModel):
    """
    维度摘要

    汇总某一冲突维度的整体情况。

    Attributes:
        dimension_id: 维度 ID
        dimension_name: 维度名称
        conflict_count: 冲突数量
        alignment_count: 对齐数量
        tension_count: 张力数量
        dominant_position: 主导立场（如果存在）
        participants: 涉及的参与者列表
    """

    model_config = {"extra": "forbid"}

    dimension_id: str = Field(
        description="维度 ID",
    )
    dimension_name: str = Field(
        description="维度名称",
    )
    conflict_count: int = Field(
        default=0,
        ge=0,
        description="冲突数量",
    )
    alignment_count: int = Field(
        default=0,
        ge=0,
        description="对齐数量",
    )
    tension_count: int = Field(
        default=0,
        ge=0,
        description="张力数量",
    )
    dominant_position: Optional[str] = Field(
        default=None,
        description="主导立场",
    )
    participants: list[str] = Field(
        default_factory=list,
        description="涉及的参与者列表",
    )


class StructuredConflictAnalysis(BaseModel):
    """
    结构化冲突分析结果

    G2 V2 输出的核心数据结构。

    Attributes:
        pairwise_analyses: 两两分析结果列表
        dimension_summaries: 各维度摘要
        stance_signals: 所有参与者的立场信号
        overall_conflict_level: 整体冲突程度
        overall_alignment_level: 整体对齐程度
        key_conflicts: 关键冲突列表（按严重程度排序）
        key_alignments: 关键对齐点列表
        recommendation: 基于分析的推荐策略
    """

    model_config = {"extra": "forbid"}

    pairwise_analyses: list[PairwiseConflict] = Field(
        default_factory=list,
        description="两两分析结果列表",
    )
    dimension_summaries: list[DimensionSummary] = Field(
        default_factory=list,
        description="各维度摘要",
    )
    stance_signals: list[StanceSignal] = Field(
        default_factory=list,
        description="所有参与者的立场信号",
    )
    overall_conflict_level: Literal["none", "low", "medium", "high", "critical"] = Field(
        default="none",
        description="整体冲突程度",
    )
    overall_alignment_level: Literal["none", "low", "medium", "high"] = Field(
        default="none",
        description="整体对齐程度",
    )
    key_conflicts: list[PairwiseConflict] = Field(
        default_factory=list,
        description="关键冲突列表",
    )
    key_alignments: list[PairwiseConflict] = Field(
        default_factory=list,
        description="关键对齐点列表",
    )
    recommendation: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="基于分析的推荐策略",
    )

    def get_conflicts_for_participant(self, participant_id: str) -> list[PairwiseConflict]:
        """
        获取涉及指定参与者的所有冲突

        Args:
            participant_id: 参与者 ID

        Returns:
            list[PairwiseConflict]: 冲突列表
        """
        return [
            pa
            for pa in self.pairwise_analyses
            if pa.conflict_type == "conflict"
            and (pa.participant_a == participant_id or pa.participant_b == participant_id)
        ]

    def get_alignments_for_participant(self, participant_id: str) -> list[PairwiseConflict]:
        """
        获取涉及指定参与者的所有对齐

        Args:
            participant_id: 参与者 ID

        Returns:
            list[PairwiseConflict]: 对齐列表
        """
        return [
            pa
            for pa in self.pairwise_analyses
            if pa.conflict_type == "alignment"
            and (pa.participant_a == participant_id or pa.participant_b == participant_id)
        ]

    def get_stance_for_participant(
        self, participant_id: str, dimension_id: str
    ) -> Optional[StanceSignal]:
        """
        获取指定参与者在指定维度上的立场

        Args:
            participant_id: 参与者 ID
            dimension_id: 维度 ID

        Returns:
            Optional[StanceSignal]: 立场信号，未找到返回 None
        """
        for signal in self.stance_signals:
            if signal.participant_id == participant_id and signal.dimension_id == dimension_id:
                return signal
        return None


__all__ = [
    "PairwiseConflict",
    "DimensionSummary",
    "StructuredConflictAnalysis",
]