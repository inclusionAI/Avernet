"""
Fallback Reason V2 Model - 结构化降级原因

Phase D: Unified Evidence Layer

表示证据层中的降级原因和降级链路追踪。

设计原则：
- 统一各模式(G1/G2/G5)的降级原因表达
- 支持降级链路追踪
- 与 Evidence 模型集成
- 便于审计和可观测性

约束：
- 内部模型，不暴露到API
- 所有降级必须有明确的 reason_code
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from src.domain.models.evidence import EvidenceSource


class FallbackReasonCode(str, Enum):
    """
    降级原因枚举

    按降级类型分组
    """

    # === 检索层降级 ===
    DENSE_RETRIEVAL_UNAVAILABLE = "dense_retrieval_unavailable"      # Dense 检索不可用
    DENSE_RETRIEVAL_TIMEOUT = "dense_retrieval_timeout"              # Dense 检索超时
    DENSE_RETRIEVAL_EMPTY = "dense_retrieval_empty"                  # Dense 检索结果为空
    SPARSE_RETRIEVAL_FALLBACK = "sparse_retrieval_fallback"          # 降级到稀疏检索

    # === Embedding 层降级 ===
    EMBEDDING_SERVICE_UNAVAILABLE = "embedding_service_unavailable"  # Embedding 服务不可用
    EMBEDDING_SERVICE_TIMEOUT = "embedding_service_timeout"          # Embedding 服务超时
    EMBEDDING_SERVICE_ERROR = "embedding_service_error"              # Embedding 服务错误
    NO_PROFILE_EMBEDDING = "no_profile_embedding"                    # Profile 无 Embedding

    # === Taxonomy 层降级 ===
    TAXONOMY_PRIOR_FALLBACK = "taxonomy_prior_fallback"              # 降级到 Taxonomy 先验
    TAXONOMY_NOT_FOUND = "taxonomy_not_found"                        # Taxonomy 未找到匹配

    # === LLM 层降级 ===
    LLM_SERVICE_UNAVAILABLE = "llm_service_unavailable"              # LLM 服务不可用
    LLM_SERVICE_TIMEOUT = "llm_service_timeout"                      # LLM 服务超时
    LLM_SERVICE_ERROR = "llm_service_error"                          # LLM 服务错误
    LLM_TO_RULE_FALLBACK = "llm_to_rule_fallback"                    # LLM 降级到规则

    # === Registry 层降级 ===
    REGISTRY_FILTER_DISABLED = "registry_filter_disabled"            # Registry 过滤被禁用
    REGISTRY_EMPTY_CANDIDATES = "registry_empty_candidates"          # Registry 过滤后无候选

    # === 模式特定降级 ===
    G5_BASIC_FALLBACK = "g5_basic_fallback"                          # G5 降级到基础处理
    G2_BASIC_FALLBACK = "g2_basic_fallback"                          # G2 降级到基础处理
    G1_BASIC_FALLBACK = "g1_basic_fallback"                          # G1 降级到基础处理

    # === Feature Flag 降级 ===
    FEATURE_FLAG_DISABLED = "feature_flag_disabled"                  # Feature Flag 关闭
    FEATURE_FLAG_FALLBACK = "feature_flag_fallback"                  # Feature Flag 降级

    # === 通用降级 ===
    RULE_BASED_FALLBACK = "rule_based_fallback"                      # 降级到规则
    TIMEOUT_FALLBACK = "timeout_fallback"                            # 超时降级
    ERROR_FALLBACK = "error_fallback"                                # 错误降级
    EMPTY_RESULT_FALLBACK = "empty_result_fallback"                  # 空结果降级


class FallbackChain(BaseModel):
    """
    降级链路

    记录从主路径到降级路径的完整链路。

    例如：
    - DENSE_RETRIEVAL -> SPARSE_RETRIEVAL -> TAXONOMY_PRIOR
    - LLM_INFERENCE -> RULE_BASED
    """

    # 链路节点
    sources: list[EvidenceSource] = Field(
        default_factory=list,
        description="降级链路（按顺序从主路径到降级路径）"
    )

    # 当前活跃源
    active_source: Optional[EvidenceSource] = Field(
        default=None,
        description="当前活跃的证据来源"
    )

    # 降级原因映射
    fallback_reasons: dict[str, FallbackReasonCode] = Field(
        default_factory=dict,
        description="每次降级的原因 {from_source: reason_code}"
    )

    # 时间戳
    timestamps: list[datetime] = Field(
        default_factory=list,
        description="每个降级节点的时间戳"
    )

    model_config = {
        "extra": "forbid",
    }

    def add_fallback(
        self,
        from_source: EvidenceSource,
        to_source: EvidenceSource,
        reason: FallbackReasonCode,
    ) -> None:
        """
        添加降级节点

        Args:
            from_source: 降级前的源
            to_source: 降级后的源
            reason: 降级原因
        """
        if not self.sources:
            self.sources.append(from_source)

        self.sources.append(to_source)
        self.active_source = to_source
        self.fallback_reasons[from_source.value] = reason
        self.timestamps.append(datetime.now())

    def get_chain_length(self) -> int:
        """获取降级链路长度"""
        return len(self.sources)

    def is_fallback_active(self) -> bool:
        """是否有降级发生"""
        return len(self.sources) > 1

    def get_fallback_depth(self) -> int:
        """获取降级深度（降级次数）"""
        return max(0, len(self.sources) - 1)

    def to_summary(self) -> dict[str, Any]:
        """生成摘要"""
        return {
            "chain": [s.value for s in self.sources],
            "active_source": self.active_source.value if self.active_source else None,
            "depth": self.get_fallback_depth(),
            "reasons": {k: v.value for k, v in self.fallback_reasons.items()},
        }


class FallbackReasonV2(BaseModel):
    """
    结构化降级原因 V2

    用于 Evidence 层统一记录降级原因，便于：
    - 审计
    - 可观测性
    - 调试
    - 归因分析

    设计原则：
    - 结构化：每个降级都有明确的 code、source、description
    - 可追溯：记录完整的降级链路
    - 可聚合：便于统计降级原因分布
    """

    # 基础信息
    reason_code: FallbackReasonCode = Field(description="降级原因代码")
    mode: Literal["G1", "G2", "G5"] = Field(description="所属模式")
    affected_component: str = Field(description="受影响组件")

    # 降级描述
    description: str = Field(description="降级描述")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="降级详细信息"
    )

    # 降级链路
    fallback_chain: FallbackChain = Field(
        default_factory=FallbackChain,
        description="降级链路"
    )

    # 相关源
    from_source: Optional[EvidenceSource] = Field(
        default=None,
        description="降级前的证据源"
    )
    to_source: Optional[EvidenceSource] = Field(
        default=None,
        description="降级后的证据源"
    )

    # 上下文
    request_id: Optional[str] = Field(default=None, description="请求ID")
    participant_id: Optional[str] = Field(default=None, description="参与者ID")
    question: Optional[str] = Field(default=None, description="关联问题")

    # 影响
    impact_level: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="降级影响级别"
    )
    degrade_quality: bool = Field(
        default=True,
        description="是否影响质量"
    )

    # 元数据
    timestamp: datetime = Field(default_factory=datetime.now, description="降级时间")
    latency_ms: Optional[int] = Field(default=None, description="降级前操作耗时")

    model_config = {
        "extra": "forbid",
    }

    def set_fallback_chain(
        self,
        from_source: EvidenceSource,
        to_source: EvidenceSource,
    ) -> None:
        """
        设置降级链路

        Args:
            from_source: 降级前的源
            to_source: 降级后的源
        """
        self.from_source = from_source
        self.to_source = to_source
        self.fallback_chain.add_fallback(
            from_source=from_source,
            to_source=to_source,
            reason=self.reason_code,
        )

    def to_log_dict(self) -> dict[str, Any]:
        """
        转换为日志字典

        用于结构化日志输出
        """
        return {
            "reason_code": self.reason_code.value,
            "mode": self.mode,
            "affected_component": self.affected_component,
            "description": self.description,
            "from_source": self.from_source.value if self.from_source else None,
            "to_source": self.to_source.value if self.to_source else None,
            "impact_level": self.impact_level,
            "chain_depth": self.fallback_chain.get_fallback_depth(),
            "request_id": self.request_id,
            "participant_id": self.participant_id,
            "latency_ms": self.latency_ms,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_evidence_context(self) -> dict[str, Any]:
        """
        转换为 Evidence 上下文

        用于 Evidence.provenance 字段
        """
        return {
            "fallback": True,
            "reason_code": self.reason_code.value,
            "fallback_chain": self.fallback_chain.to_summary(),
            "impact_level": self.impact_level,
        }


# === 便捷工厂函数 ===

def create_dense_to_sparse_fallback(
    mode: Literal["G1", "G2", "G5"],
    reason: FallbackReasonCode = FallbackReasonCode.DENSE_RETRIEVAL_EMPTY,
    request_id: Optional[str] = None,
    latency_ms: Optional[int] = None,
) -> FallbackReasonV2:
    """创建 Dense -> Sparse 降级原因"""
    fallback = FallbackReasonV2(
        reason_code=reason,
        mode=mode,
        affected_component="evidence_retrieval",
        description="Dense 检索降级到 Sparse 检索",
        request_id=request_id,
        latency_ms=latency_ms,
    )
    fallback.set_fallback_chain(
        from_source=EvidenceSource.DENSE_RETRIEVAL,
        to_source=EvidenceSource.SPARSE_RETRIEVAL,
    )
    return fallback


def create_sparse_to_taxonomy_fallback(
    mode: Literal["G1", "G2", "G5"],
    reason: FallbackReasonCode = FallbackReasonCode.TAXONOMY_PRIOR_FALLBACK,
    request_id: Optional[str] = None,
    latency_ms: Optional[int] = None,
) -> FallbackReasonV2:
    """创建 Sparse -> Taxonomy 降级原因"""
    fallback = FallbackReasonV2(
        reason_code=reason,
        mode=mode,
        affected_component="evidence_retrieval",
        description="Sparse 检索降级到 Taxonomy 先验",
        request_id=request_id,
        latency_ms=latency_ms,
    )
    fallback.set_fallback_chain(
        from_source=EvidenceSource.SPARSE_RETRIEVAL,
        to_source=EvidenceSource.TAXONOMY_PRIOR,
    )
    return fallback


def create_llm_to_rule_fallback(
    mode: Literal["G1", "G2", "G5"],
    reason: FallbackReasonCode = FallbackReasonCode.LLM_TO_RULE_FALLBACK,
    request_id: Optional[str] = None,
    latency_ms: Optional[int] = None,
) -> FallbackReasonV2:
    """创建 LLM -> Rule 降级原因"""
    fallback = FallbackReasonV2(
        reason_code=reason,
        mode=mode,
        affected_component="llm_inference",
        description="LLM 推断降级到规则计算",
        impact_level="high",
        request_id=request_id,
        latency_ms=latency_ms,
    )
    fallback.set_fallback_chain(
        from_source=EvidenceSource.LLM_INFERENCE,
        to_source=EvidenceSource.RULE_BASED,
    )
    return fallback


def create_embedding_unavailable_fallback(
    mode: Literal["G1", "G2", "G5"],
    reason: FallbackReasonCode = FallbackReasonCode.EMBEDDING_SERVICE_UNAVAILABLE,
    request_id: Optional[str] = None,
    latency_ms: Optional[int] = None,
) -> FallbackReasonV2:
    """创建 Embedding 不可用降级原因"""
    fallback = FallbackReasonV2(
        reason_code=reason,
        mode=mode,
        affected_component="embedding_service",
        description="Embedding 服务不可用，降级到文本匹配",
        impact_level="high",
        request_id=request_id,
        latency_ms=latency_ms,
    )
    return fallback


__all__ = [
    "FallbackReasonCode",
    "FallbackChain",
    "FallbackReasonV2",
    "create_dense_to_sparse_fallback",
    "create_sparse_to_taxonomy_fallback",
    "create_llm_to_rule_fallback",
    "create_embedding_unavailable_fallback",
]