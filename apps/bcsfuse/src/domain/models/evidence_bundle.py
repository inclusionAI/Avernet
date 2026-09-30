"""
Evidence Bundle Model - 证据聚合结果

Phase D: Unified Evidence Layer

表示单个请求的证据聚合结果，包含：
- 证据列表
- 聚合分数
- 来源分布
- 贡献分析

约束：
- 内部模型，不暴露到API
- 支持增量添加证据
- 支持贡献度分析
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from src.domain.models.evidence import (
    Evidence,
    EvidenceSourceDistribution,
    EvidenceType,
)


class EvidenceContribution(BaseModel):
    """证据贡献度分析"""

    evidence_id: str = Field(description="证据ID")
    contribution_ratio: float = Field(ge=0.0, le=1.0, description="贡献占比")
    rank: int = Field(ge=1, description="贡献排名")


class EvidenceBundle(BaseModel):
    """
    证据聚合结果

    表示单个请求的证据聚合结果，用于：
    1. 内部决策参考
    2. 解释构建基础
    3. 评估数据来源

    设计原则：
    - 单一请求一个Bundle
    - 支持增量添加证据
    - 延迟计算聚合结果
    """

    # 基础标识
    bundle_id: str = Field(description="Bundle唯一标识")
    mode: Literal["G1", "G2", "G5"] = Field(description="所属模式")
    question: str = Field(description="原始问题")

    # 证据列表
    evidences: list[Evidence] = Field(
        default_factory=list,
        description="证据列表"
    )

    # 聚合结果（延迟计算）
    total_weight: float = Field(default=0.0, description="总权重")
    weighted_sum: float = Field(default=0.0, description="加权总和")
    normalized_score: float = Field(default=0.0, ge=0.0, le=1.0, description="归一化分数 (0.0-1.0)")

    # 是否已聚合
    is_aggregated: bool = Field(default=False, description="是否已完成聚合")

    # 来源分布
    source_distribution: EvidenceSourceDistribution = Field(
        default_factory=EvidenceSourceDistribution,
        description="证据来源分布"
    )

    # 贡献分析
    top_contributors: list[EvidenceContribution] = Field(
        default_factory=list,
        description="贡献最大的证据列表（按贡献度降序）"
    )

    # 元数据
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    aggregated_at: Optional[datetime] = Field(default=None, description="聚合完成时间")
    computation_time_ms: Optional[int] = Field(default=None, description="聚合耗时(ms)")

    # 请求上下文
    request_id: Optional[str] = Field(default=None, description="请求ID")
    participant_ids: list[str] = Field(default_factory=list, description="参与者ID列表")
    strict_participants: bool = Field(default=False, description="是否严格参与者模式")

    model_config = {
        "extra": "forbid",
    }

    def add_evidence(self, evidence: Evidence) -> None:
        """
        添加证据到Bundle

        注意：添加证据后会重置聚合状态
        """
        self.evidences.append(evidence)
        self.is_aggregated = False
        self.source_distribution.add_evidence(evidence)

    def add_evidences(self, evidences: list[Evidence]) -> None:
        """批量添加证据"""
        for evidence in evidences:
            self.add_evidence(evidence)

    def aggregate(self, normalize: bool = True) -> None:
        """
        聚合证据

        计算加权总分和贡献度分析

        Args:
            normalize: 是否归一化到[0,1]
        """
        if not self.evidences:
            self.total_weight = 0.0
            self.weighted_sum = 0.0
            self.normalized_score = 0.0
            self.is_aggregated = True
            self.aggregated_at = datetime.now()
            return

        # 计算加权和
        self.total_weight = sum(e.weight for e in self.evidences)
        self.weighted_sum = sum(e.weighted_value for e in self.evidences)

        # 归一化
        if normalize and self.total_weight > 0:
            self.normalized_score = self.weighted_sum / self.total_weight
        else:
            self.normalized_score = self.weighted_sum

        # 计算贡献度
        self._compute_contributions()

        self.is_aggregated = True
        self.aggregated_at = datetime.now()

    def _compute_contributions(self) -> None:
        """计算证据贡献度"""
        if self.weighted_sum == 0:
            return

        # 按加权值降序排序
        sorted_evidences = sorted(
            self.evidences,
            key=lambda e: e.weighted_value,
            reverse=True
        )

        # 计算贡献占比
        self.top_contributors = []
        for rank, evidence in enumerate(sorted_evidences, 1):
            contribution_ratio = evidence.weighted_value / self.weighted_sum if self.weighted_sum > 0 else 0
            self.top_contributors.append(EvidenceContribution(
                evidence_id=evidence.evidence_id,
                contribution_ratio=contribution_ratio,
                rank=rank,
            ))

    def get_evidence_by_id(self, evidence_id: str) -> Optional[Evidence]:
        """根据ID获取证据"""
        for evidence in self.evidences:
            if evidence.evidence_id == evidence_id:
                return evidence
        return None

    def get_evidences_by_type(self, evidence_type: EvidenceType) -> list[Evidence]:
        """根据类型获取证据列表"""
        return [e for e in self.evidences if e.evidence_type == evidence_type]

    def get_evidences_by_participant(self, participant_id: str) -> list[Evidence]:
        """根据参与者ID获取证据列表"""
        return [e for e in self.evidences if e.participant_id == participant_id]

    def get_top_k_contributors(self, k: int = 3) -> list[EvidenceContribution]:
        """获取Top-K贡献者"""
        return self.top_contributors[:k]

    def get_aggregation_summary(self) -> dict[str, Any]:
        """
        获取聚合摘要

        用于内部日志和调试
        """
        return {
            "bundle_id": self.bundle_id,
            "mode": self.mode,
            "evidence_count": len(self.evidences),
            "total_weight": self.total_weight,
            "weighted_sum": self.weighted_sum,
            "normalized_score": round(self.normalized_score, 4),
            "is_aggregated": self.is_aggregated,
            "top_contributors": [
                {"id": c.evidence_id, "ratio": round(c.contribution_ratio, 4)}
                for c in self.top_contributors[:3]
            ],
        }

    def to_explanation_context(self) -> dict[str, Any]:
        """
        生成解释上下文

        用于ExplanationBuilderV2
        """
        return {
            "question": self.question,
            "mode": self.mode,
            "score": self.normalized_score,
            "top_factors": [
                {
                    "type": self.get_evidence_by_id(c.evidence_id).evidence_type.value
                    if self.get_evidence_by_id(c.evidence_id) else "unknown",
                    "contribution": round(c.contribution_ratio * 100, 1),
                }
                for c in self.top_contributors[:5]
            ],
            "source_distribution": self.source_distribution.by_source,
            "participant_count": len(self.participant_ids),
            "strict_mode": self.strict_participants,
        }


__all__ = [
    "EvidenceContribution",
    "EvidenceBundle",
]