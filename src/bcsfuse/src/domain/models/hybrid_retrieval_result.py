"""
Hybrid Retrieval Result Model

混合召回结果模型，支持 Dense/Sparse/Hybrid 三种召回模式。

Phase E 核心模型。

设计原则：
- 单一来源：所有 HybridScore 计算必须通过 RetrievalScorer
- 与现有 RetrievalResult (M5) 共存，不破坏现有代码
- 支持 Dense embedding 召回 + Sparse 文本召回 + Structured 过滤
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.domain.models.hybrid_score import HybridScore


class RetrievalSource(str, Enum):
    """召回来源"""

    DENSE = "dense"  # Dense 向量召回（embedding 主召回）
    SPARSE = "sparse"  # Sparse 文本召回（BM25/关键词）
    HYBRID = "hybrid"  # 混合召回（Dense + Sparse）
    STRUCTURED = "structured"  # 纯结构化过滤（无语义匹配）


class FallbackReason(str, Enum):
    """降级原因"""

    NONE = "none"  # 未降级
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"  # Embedding 服务不可用
    INDEX_NOT_READY = "index_not_ready"  # 向量索引未就绪
    EMPTY_DENSE_RESULT = "empty_dense_result"  # Dense 召回无结果
    TIMEOUT = "timeout"  # 超时
    FEATURE_FLAG_DISABLED = "feature_flag_disabled"  # Feature Flag 未启用


class RetrievalCandidate(BaseModel):
    """召回候选"""

    profile_key: str = Field(description="Profile 标识")

    # 评分信息（单一来源：RetrievalScorer）
    score: float = Field(ge=0.0, le=1.0, description="最终分数")
    hybrid_score: Optional[HybridScore] = Field(
        default=None,
        description="混合评分详情（可选，需 ENABLE_RETRIEVAL_SCORE_BREAKDOWN=true）",
    )

    # 召回来源
    source: RetrievalSource = Field(
        default=RetrievalSource.HYBRID,
        description="主要召回来源",
    )

    # Worker 信息
    worker_id: Optional[str] = Field(default=None, description="Worker ID")
    profile_name: Optional[str] = Field(default=None, description="Profile 名称")

    # 能力信息（用于 downstream 消费）
    capabilities: list[str] = Field(default_factory=list, description="能力列表")
    domains: list[str] = Field(default_factory=list, description="领域列表")
    scenarios: list[str] = Field(default_factory=list, description="场景列表")

    # 匹配详情
    matched_terms: list[str] = Field(
        default_factory=list,
        description="匹配的关键词（Sparse 召回时填充）",
    )
    matched_capabilities: list[str] = Field(
        default_factory=list,
        description="匹配的能力项",
    )

    # 元数据
    metadata: dict[str, Any] = Field(default_factory=dict, description="其他元数据")

    def get_worker_id(self) -> str:
        """从 profile_key 提取 worker_id

        注意: worker_id 本身可能包含冒号，所以取除最后一部分外的所有内容
        """
        if self.worker_id:
            return self.worker_id
        parts = self.profile_key.split(":")
        if len(parts) > 1:
            return ":".join(parts[:-1])
        return self.profile_key


class HybridRetrievalResult(BaseModel):
    """
    混合召回结果

    单一来源要求：
    - 所有 HybridRetrievalResult 的构造必须通过 HybridRetrievalService
    - 评分计算必须通过 RetrievalScorer

    Fields:
        question: 问题文本
        query_embedding: 问题 embedding（Dense 召回时填充）
        candidates: 召回候选列表
        source: 主要召回来源
        fallback_occurred: 是否发生降级
        fallback_reason: 降级原因
        fallback_chain: 降级链路记录
        latency_ms: 总召回耗时
        dense_latency_ms: Dense 召回耗时
        sparse_latency_ms: Sparse 召回耗时
        structured_latency_ms: 结构化过滤耗时
        total_candidates: 候选总数
        dense_candidates: Dense 召回候选数
        sparse_candidates: Sparse 召回候选数
        flags_enabled: 启用的 feature flags
        timestamp: 召回时间
        metadata: 其他元数据
    """

    question: str = Field(description="问题文本")
    query_embedding: Optional[list[float]] = Field(
        default=None,
        description="问题 embedding（可选，Dense 召回时填充）",
    )

    # 候选列表
    candidates: list[RetrievalCandidate] = Field(
        default_factory=list,
        description="召回候选列表",
    )

    # 召回元数据
    source: RetrievalSource = Field(
        default=RetrievalSource.HYBRID,
        description="主要召回来源",
    )
    fallback_occurred: bool = Field(
        default=False,
        description="是否发生降级",
    )
    fallback_reason: FallbackReason = Field(
        default=FallbackReason.NONE,
        description="降级原因",
    )
    fallback_chain: list[str] = Field(
        default_factory=list,
        description="降级链路记录（如 ['dense', 'sparse', 'structured']）",
    )

    # 性能指标
    latency_ms: float = Field(default=0.0, description="召回耗时（毫秒）")
    dense_latency_ms: Optional[float] = Field(default=None, description="Dense 召回耗时")
    sparse_latency_ms: Optional[float] = Field(default=None, description="Sparse 召回耗时")
    structured_latency_ms: Optional[float] = Field(
        default=None, description="结构化过滤耗时"
    )

    # 召回统计
    total_candidates: int = Field(default=0, description="候选总数")
    dense_candidates: int = Field(default=0, description="Dense 召回候选数")
    sparse_candidates: int = Field(default=0, description="Sparse 召回候选数")

    # Feature Flags 记录
    flags_enabled: list[str] = Field(
        default_factory=list,
        description="启用的 feature flags",
    )

    # 时间戳
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="召回时间",
    )

    # 其他信息
    metadata: dict[str, Any] = Field(default_factory=dict, description="其他元数据")

    def is_empty(self) -> bool:
        """是否为空结果"""
        return len(self.candidates) == 0

    def get_top_k(self, k: int) -> list[RetrievalCandidate]:
        """获取 Top-K 候选"""
        return self.candidates[:k]

    def get_by_source(self, source: RetrievalSource) -> list[RetrievalCandidate]:
        """按召回来源过滤候选"""
        return [c for c in self.candidates if c.source == source]


class HybridRetrievalContext(BaseModel):
    """
    混合召回上下文（用于传递给 RetrievalScorer）

    封装召回所需的所有参数和配置。

    Fields:
        question: 问题文本
        query_embedding: 问题 embedding（如果已计算）
        required_capabilities: 必需的能力
        required_domains: 必需的领域
        required_scenarios: 必需的场景
        profile_keys: 限定候选范围（strict_participants）
        strict: 是否严格模式
        top_k: 召回数量
        min_score: 最小分数阈值
        enable_dense: 启用 Dense 召回
        enable_sparse: 启用 Sparse 召回
        enable_structured: 启用结构化过滤
        timeout_ms: 超时时间（毫秒）
        metadata: 其他元数据
    """

    question: str = Field(description="问题文本")
    query_embedding: Optional[list[float]] = Field(
        default=None,
        description="问题 embedding（如果已计算）",
    )

    # 需求约束
    required_capabilities: list[str] = Field(
        default_factory=list,
        description="必需的能力",
    )
    required_domains: list[str] = Field(
        default_factory=list,
        description="必需的领域",
    )
    required_scenarios: list[str] = Field(
        default_factory=list,
        description="必需的场景",
    )

    # 候选约束
    profile_keys: Optional[list[str]] = Field(
        default=None,
        description="限定候选范围（strict_participants）",
    )
    strict: bool = Field(
        default=False,
        description="是否严格模式",
    )

    # 召回配置
    top_k: int = Field(default=10, description="召回数量")
    min_score: float = Field(default=0.0, ge=0.0, le=1.0, description="最小分数阈值")

    # Feature Flags
    enable_dense: bool = Field(default=True, description="启用 Dense 召回")
    enable_sparse: bool = Field(default=True, description="启用 Sparse 召回")
    enable_structured: bool = Field(default=True, description="启用结构化过滤")

    # 超时配置
    timeout_ms: Optional[int] = Field(default=None, description="超时时间（毫秒）")

    # 元数据
    metadata: dict[str, Any] = Field(default_factory=dict, description="其他元数据")


__all__ = [
    "RetrievalSource",
    "FallbackReason",
    "RetrievalCandidate",
    "HybridRetrievalResult",
    "HybridRetrievalContext",
]