"""
Attribution Report - 降级归因报告

Phase F: 结构化记录失败原因

设计原则：
- 区分不同类型的失败原因
- 支持链路追踪
- 支持自动化分析
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class FallbackType(str, Enum):
    """降级类型"""

    DENSE_UNAVAILABLE = "dense_unavailable"  # Dense 不可用
    INDEX_MISSING = "index_missing"  # 索引缺失
    SPARSE_FALLBACK = "sparse_fallback"  # Sparse 降级
    STRICT_CONSTRAINT = "strict_constraint"  # Strict 约束触发
    NO_MATCHING_CANDIDATES = "no_matching_candidates"  # 无匹配候选
    SERVICE_TIMEOUT = "service_timeout"  # 服务超时
    PARSING_ERROR = "parsing_error"  # 解析错误
    LLM_DEGRADED = "llm_degraded"  # LLM 降级
    FEATURE_FLAG_DISABLED = "feature_flag_disabled"  # Feature flag 未启用
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"  # Embedding 不可用


class AttributionLevel(str, Enum):
    """归因级别"""

    CRITICAL = "critical"  # 关键问题（阻塞主链路）
    WARNING = "warning"  # 警告（降级但有恢复）
    INFO = "info"  # 信息（正常降级）


class FallbackChain(BaseModel):
    """降级链路"""

    steps: list[str] = Field(
        default_factory=list,
        description="降级步骤列表"
    )
    final_source: str = Field(default="unknown", description="最终来源")
    fallback_count: int = Field(default=0, description="降级次数")


class AttributionReport(BaseModel):
    """
    降级归因报告

    记录完整的降级链路和原因。

    Fields:
        attribution_id: 归因 ID
        timestamp: 归因时间
        fallback_type: 降级类型
        level: 归因级别
        description: 描述
        fallback_chain: 降级链路
        root_cause: 根因分析
        impact: 影响评估
        recommendations: 改进建议
        metadata: 其他元数据
    """

    attribution_id: str = Field(description="归因 ID")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="归因时间"
    )

    # 基本信息
    fallback_type: FallbackType = Field(description="降级类型")
    level: AttributionLevel = Field(
        default=AttributionLevel.WARNING,
        description="归因级别"
    )
    description: str = Field(default="", description="描述")

    # 降级链路
    fallback_chain: FallbackChain = Field(
        default_factory=FallbackChain,
        description="降级链路"
    )

    # 根因分析
    root_cause: Optional[str] = Field(default=None, description="根因")
    contributing_factors: list[str] = Field(
        default_factory=list,
        description="贡献因素"
    )

    # 影响评估
    impact: dict[str, Any] = Field(
        default_factory=dict,
        description="影响评估"
    )

    # 改进建议
    recommendations: list[str] = Field(
        default_factory=list,
        description="改进建议"
    )

    # 其他信息
    metadata: dict[str, Any] = Field(default_factory=dict, description="其他元数据")

    def to_summary_dict(self) -> dict[str, Any]:
        """转换为摘要字典"""
        return {
            "attribution_id": self.attribution_id,
            "timestamp": self.timestamp.isoformat(),
            "fallback_type": self.fallback_type.value,
            "level": self.level.value,
            "description": self.description,
            "fallback_count": self.fallback_chain.fallback_count,
            "final_source": self.fallback_chain.final_source,
        }

    @classmethod
    def create_dense_unavailable(
        cls,
        attribution_id: str,
        reason: str,
    ) -> "AttributionReport":
        """创建 Dense 不可用归因"""
        return cls(
            attribution_id=attribution_id,
            fallback_type=FallbackType.DENSE_UNAVAILABLE,
            level=AttributionLevel.WARNING,
            description=f"Dense retrieval unavailable: {reason}",
            root_cause=reason,
            recommendations=[
                "Check embedding provider availability",
                "Check feature flag ENABLE_DENSE_RETRIEVAL",
                "Verify profile embedding index is built",
            ],
        )

    @classmethod
    def create_index_missing(
        cls,
        attribution_id: str,
    ) -> "AttributionReport":
        """创建索引缺失归因"""
        return cls(
            attribution_id=attribution_id,
            fallback_type=FallbackType.INDEX_MISSING,
            level=AttributionLevel.WARNING,
            description="Profile embedding index not available",
            root_cause="Index not built or empty",
            recommendations=[
                "Run profile embedding index builder",
                "Check ENABLE_PROFILE_EMBEDDING_INDEX flag",
                "Verify embedding provider is configured",
            ],
        )

    @classmethod
    def create_strict_constraint(
        cls,
        attribution_id: str,
        profile_keys: list[str],
    ) -> "AttributionReport":
        """创建 strict 约束归因"""
        return cls(
            attribution_id=attribution_id,
            fallback_type=FallbackType.STRICT_CONSTRAINT,
            level=AttributionLevel.INFO,
            description=f"Strict constraint triggered for {len(profile_keys)} profiles",
            root_cause="strict=true mode restricts candidates to explicit participants",
            impact={
                "restricted_candidates": len(profile_keys),
            },
            recommendations=[
                "Verify participant availability",
                "Consider relaxing strict mode if appropriate",
            ],
        )

    @classmethod
    def create_no_matching_candidates(
        cls,
        attribution_id: str,
        reason: str,
    ) -> "AttributionReport":
        """创建无匹配候选归因"""
        return cls(
            attribution_id=attribution_id,
            fallback_type=FallbackType.NO_MATCHING_CANDIDATES,
            level=AttributionLevel.WARNING,
            description=f"No matching candidates found: {reason}",
            root_cause=reason,
            recommendations=[
                "Review filtering criteria",
                "Check candidate availability",
                "Consider expanding search scope",
            ],
        )


__all__ = [
    "FallbackType",
    "AttributionLevel",
    "FallbackChain",
    "AttributionReport",
]