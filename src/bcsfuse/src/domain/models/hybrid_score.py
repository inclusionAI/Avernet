"""
HybridScore - Hybrid Retrieval 统一评分模型

Phase E: 作为评分公式的单一来源

设计原则：
- 唯一评分公式定义位置
- 支持 dense/sparse/hybrid 三种来源
- 支持 score breakdown 输出
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ScoreSource(str, Enum):
    """评分来源类型"""
    DENSE = "dense"          # Dense retrieval (embedding similarity)
    SPARSE = "sparse"        # Sparse retrieval (BM25/keyword)
    HYBRID = "hybrid"        # Hybrid fusion (dense + sparse)
    LEGACY = "legacy"        # Legacy scorer (taxonomy + text match)
    FALLBACK = "fallback"    # Fallback when primary fails


@dataclass
class DenseScore:
    """
    Dense retrieval 评分
    
    Attributes:
        similarity: 原始相似度分数（来自 FAISS）
        normalized: 归一化后的分数 (0-1)
        model_version: Embedding 模型版本
    """
    similarity: float
    normalized: float = 0.0
    model_version: str = ""
    
    def __post_init__(self):
        if self.normalized == 0.0:
            # FAISS 内积相似度归一化到 [0, 1]
            self.normalized = max(0.0, min(1.0, (self.similarity + 1.0) / 2.0))


@dataclass
class SparseScore:
    """
    Sparse retrieval 评分
    
    Attributes:
        bm25_score: BM25 原始分数
        normalized: 归一化后的分数 (0-1)
        matched_terms: 匹配的关键词列表
    """
    bm25_score: float
    normalized: float = 0.0
    matched_terms: list[str] = field(default_factory=list)
    
    def __post_init__(self):
        if self.normalized == 0.0 and self.bm25_score > 0:
            # BM25 分数归一化（假设 max_bm25 ≈ 20）
            self.normalized = min(1.0, self.bm25_score / 20.0)


@dataclass
class HybridScore:
    """
    Hybrid Retrieval 统一评分模型
    
    单一来源评分公式：
        final_score = alpha * dense_score + (1 - alpha) * sparse_score
    
    其中：
        - alpha: dense 权重（默认 0.6）
        - dense_score: 来自 DenseScore.normalized
        - sparse_score: 来自 SparseScore.normalized
    
    当某来源不可用时：
        - dense unavailable: final_score = sparse_score
        - sparse unavailable: final_score = dense_score
        - both unavailable: final_score = 0.0 (fallback/empty result)
    
    Attributes:
        final_score: 最终融合分数 (0-1)
        source: 评分来源
        dense: Dense retrieval 评分（可选）
        sparse: Sparse retrieval 评分（可选）
        alpha: Dense 权重
        fallback_reason: 降级原因（如果有）
        metadata: 额外元数据
    """
    final_score: float
    source: ScoreSource
    dense: Optional[DenseScore] = None
    sparse: Optional[SparseScore] = None
    alpha: float = 0.6
    fallback_reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dense_only(
        cls,
        dense: DenseScore,
        fallback_reason: Optional[str] = None,
    ) -> "HybridScore":
        """
        仅使用 dense 分数创建 HybridScore
        
        Args:
            dense: Dense retrieval 评分
            fallback_reason: 降级原因（如果有）
        
        Returns:
            HybridScore 实例
        """
        return cls(
            final_score=dense.normalized,
            source=ScoreSource.DENSE if not fallback_reason else ScoreSource.FALLBACK,
            dense=dense,
            fallback_reason=fallback_reason,
        )
    
    @classmethod
    def from_sparse_only(
        cls,
        sparse: SparseScore,
        fallback_reason: Optional[str] = None,
    ) -> "HybridScore":
        """
        仅使用 sparse 分数创建 HybridScore
        
        Args:
            sparse: Sparse retrieval 评分
            fallback_reason: 降级原因（如果有）
        
        Returns:
            HybridScore 实例
        """
        return cls(
            final_score=sparse.normalized,
            source=ScoreSource.SPARSE if not fallback_reason else ScoreSource.FALLBACK,
            sparse=sparse,
            fallback_reason=fallback_reason,
        )
    
    @classmethod
    def from_hybrid(
        cls,
        dense: DenseScore,
        sparse: SparseScore,
        alpha: float = 0.6,
    ) -> "HybridScore":
        """
        使用 hybrid 融合公式创建 HybridScore
        
        公式: final_score = alpha * dense + (1 - alpha) * sparse
        
        Args:
            dense: Dense retrieval 评分
            sparse: Sparse retrieval 评分
            alpha: Dense 权重（默认 0.6）
        
        Returns:
            HybridScore 实例
        """
        final_score = alpha * dense.normalized + (1 - alpha) * sparse.normalized
        return cls(
            final_score=final_score,
            source=ScoreSource.HYBRID,
            dense=dense,
            sparse=sparse,
            alpha=alpha,
        )
    
    @classmethod
    def from_legacy(
        cls,
        score: float,
        metadata: Optional[dict[str, Any]] = None,
    ) -> "HybridScore":
        """
        从 legacy scorer 创建 HybridScore
        
        Args:
            score: Legacy 评分
            metadata: 额外元数据
        
        Returns:
            HybridScore 实例
        """
        return cls(
            final_score=score,
            source=ScoreSource.LEGACY,
            metadata=metadata or {},
        )
    
    @classmethod
    def empty(cls, reason: str = "no_candidates") -> "HybridScore":
        """
        创建空结果
        
        Args:
            reason: 原因说明
        
        Returns:
            HybridScore 实例
        """
        return cls(
            final_score=0.0,
            source=ScoreSource.FALLBACK,
            fallback_reason=reason,
        )
    
    def to_breakdown_dict(self) -> dict[str, Any]:
        """
        转换为 breakdown 输出格式
        
        Returns:
            包含评分明细的字典
        """
        breakdown = {
            "final_score": self.final_score,
            "source": self.source.value,
        }
        
        if self.dense:
            breakdown["dense"] = {
                "similarity": self.dense.similarity,
                "normalized": self.dense.normalized,
                "model_version": self.dense.model_version,
            }
        
        if self.sparse:
            breakdown["sparse"] = {
                "bm25_score": self.sparse.bm25_score,
                "normalized": self.sparse.normalized,
                "matched_terms": self.sparse.matched_terms[:10],
            }
        
        if self.alpha != 0.6:
            breakdown["alpha"] = self.alpha
        
        if self.fallback_reason:
            breakdown["fallback_reason"] = self.fallback_reason
        
        if self.metadata:
            breakdown["metadata"] = self.metadata
        
        return breakdown


__all__ = [
    "ScoreSource",
    "DenseScore",
    "SparseScore",
    "HybridScore",
]
