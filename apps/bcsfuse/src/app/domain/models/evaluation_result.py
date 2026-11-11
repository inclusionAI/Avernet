"""
Evaluation Result - 评估结果模型

Phase F: 统一评估结果格式

设计原则：
- 记录 retrieval 和 decision 的质量评估
- 支持 fallback attribution
- 支持 sample collection
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class RetrievalQualityMetrics(BaseModel):
    """检索质量指标"""

    # 候选数量
    total_candidates: int = Field(default=0, description="总候选数")
    dense_candidates: int = Field(default=0, description="Dense 召回数")
    sparse_candidates: int = Field(default=0, description="Sparse 召回数")

    # 分数分布
    avg_score: float = Field(default=0.0, description="平均分数")
    max_score: float = Field(default=0.0, description="最高分")
    min_score: float = Field(default=0.0, description="最低分")

    # 召回来源
    source: str = Field(default="hybrid", description="主要召回来源")

    # Fallback 信息
    fallback_occurred: bool = Field(default=False, description="是否发生降级")
    fallback_reason: Optional[str] = Field(default=None, description="降级原因")


class DecisionQualityMetrics(BaseModel):
    """决策质量指标"""

    # 决策来源
    decision_source: str = Field(default="unknown", description="决策来源")

    # 证据质量
    evidence_count: int = Field(default=0, description="证据数量")
    high_quality_evidence_ratio: float = Field(
        default=0.0,
        description="高质量证据比例"
    )

    # 结构化输出
    structured_output_complete: bool = Field(
        default=False,
        description="结构化输出是否完整"
    )

    # 特殊场景
    needs_more_information: bool = Field(
        default=False,
        description="是否需要更多信息"
    )
    degraded_mode: bool = Field(default=False, description="是否降级模式")

    # LLM 质量
    llm_call_success: bool = Field(default=True, description="LLM 调用是否成功")
    parsing_success: bool = Field(default=True, description="解析是否成功")


class EvaluationResult(BaseModel):
    """
    评估结果

    记录一次完整的 retrieval + decision 评估。

    Fields:
        evaluation_id: 评估 ID
        timestamp: 评估时间
        question: 问题文本
        retrieval_metrics: 检索质量指标
        decision_metrics: 决策质量指标
        fallback_attribution: 降级归因
        metadata: 其他元数据
    """

    evaluation_id: str = Field(description="评估 ID")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="评估时间"
    )

    # 输入
    question: str = Field(description="问题文本")
    profile_keys: Optional[list[str]] = Field(
        default=None,
        description="显式 participants"
    )
    strict_mode: bool = Field(default=False, description="是否 strict 模式")

    # 检索评估
    retrieval_metrics: RetrievalQualityMetrics = Field(
        default_factory=RetrievalQualityMetrics,
        description="检索质量指标"
    )

    # 决策评估
    decision_metrics: DecisionQualityMetrics = Field(
        default_factory=DecisionQualityMetrics,
        description="决策质量指标"
    )

    # 降级归因
    fallback_attribution: Optional[dict[str, Any]] = Field(
        default=None,
        description="降级归因报告"
    )

    # 是否为样本
    is_sample: bool = Field(default=False, description="是否收集为样本")
    sample_reason: Optional[str] = Field(default=None, description="样本收集原因")

    # Feature flags
    flags_enabled: list[str] = Field(
        default_factory=list,
        description="启用的 feature flags"
    )

    # 其他信息
    metadata: dict[str, Any] = Field(default_factory=dict, description="其他元数据")

    def to_summary_dict(self) -> dict[str, Any]:
        """转换为摘要字典"""
        return {
            "evaluation_id": self.evaluation_id,
            "timestamp": self.timestamp.isoformat(),
            "question_preview": self.question[:100],
            "retrieval_source": self.retrieval_metrics.source,
            "retrieval_candidates": self.retrieval_metrics.total_candidates,
            "retrieval_fallback": self.retrieval_metrics.fallback_occurred,
            "decision_source": self.decision_metrics.decision_source,
            "decision_evidence_count": self.decision_metrics.evidence_count,
            "decision_degraded": self.decision_metrics.degraded_mode,
            "is_sample": self.is_sample,
        }


__all__ = [
    "RetrievalQualityMetrics",
    "DecisionQualityMetrics",
    "EvaluationResult",
]