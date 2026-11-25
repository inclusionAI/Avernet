"""
Feedback Sample - 反馈样本模型

Phase F: 失败/边界样本收集

设计原则：
- 沉淀高质量样本用于改进
- 支持回放和评估
- 不影响主链路性能
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SampleType(str, Enum):
    """样本类型"""

    FAILURE = "failure"  # 失败样本
    EDGE_CASE = "edge_case"  # 边界样本
    LOW_QUALITY = "low_quality"  # 低质量样本
    DEGRADED = "degraded"  # 降级样本
    FALLBACK = "fallback"  # 降级样本
    MANUAL = "manual"  # 手动标记样本


class SamplePriority(str, Enum):
    """样本优先级"""

    HIGH = "high"  # 高优先级（需要立即关注）
    MEDIUM = "medium"  # 中优先级
    LOW = "low"  # 低优先级


class FeedbackSample(BaseModel):
    """
    反馈样本

    用于沉淀失败/边界样本，后续用于改进和评估。

    Fields:
        sample_id: 样本 ID
        sample_type: 样本类型
        priority: 优先级
        timestamp: 收集时间
        question: 问题文本
        context: 上下文信息
        retrieval_result: 检索结果
        decision_result: 决策结果
        fallback_attribution: 降级归因
        metadata: 其他元数据
    """

    sample_id: str = Field(description="样本 ID")
    sample_type: SampleType = Field(description="样本类型")
    priority: SamplePriority = Field(
        default=SamplePriority.MEDIUM,
        description="优先级"
    )

    # 时间信息
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="收集时间"
    )

    # 输入
    question: str = Field(description="问题文本")
    profile_keys: Optional[list[str]] = Field(
        default=None,
        description="显式 participants"
    )
    strict_mode: bool = Field(default=False, description="是否 strict 模式")

    # 上下文
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="上下文信息"
    )

    # 检索结果
    retrieval_result: Optional[dict[str, Any]] = Field(
        default=None,
        description="检索结果快照"
    )

    # 决策结果
    decision_result: Optional[dict[str, Any]] = Field(
        default=None,
        description="决策结果快照"
    )

    # 降级归因
    fallback_attribution: Optional[dict[str, Any]] = Field(
        default=None,
        description="降级归因报告"
    )

    # 标记信息
    is_reviewed: bool = Field(default=False, description="是否已审查")
    reviewed_by: Optional[str] = Field(default=None, description="审查人")
    reviewed_at: Optional[datetime] = Field(default=None, description="审查时间")
    review_notes: Optional[str] = Field(default=None, description="审查备注")

    # 改进信息
    improvement_action: Optional[str] = Field(
        default=None,
        description="改进措施"
    )
    improvement_status: Optional[str] = Field(
        default=None,
        description="改进状态"
    )

    # 其他信息
    metadata: dict[str, Any] = Field(default_factory=dict, description="其他元数据")

    def to_summary_dict(self) -> dict[str, Any]:
        """转换为摘要字典"""
        return {
            "sample_id": self.sample_id,
            "sample_type": self.sample_type.value,
            "priority": self.priority.value,
            "timestamp": self.timestamp.isoformat(),
            "question_preview": self.question[:100],
            "is_reviewed": self.is_reviewed,
        }


__all__ = [
    "SampleType",
    "SamplePriority",
    "FeedbackSample",
]