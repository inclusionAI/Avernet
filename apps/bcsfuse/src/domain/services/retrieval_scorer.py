"""
Retrieval Scorer

Phase E: 统一评分公式（单一来源）

职责：
- 定义 Hybird Retrieval 的评分公式
- 融合 dense + sparse 分数
- 应用 bonus/penalty
- 生成最终分数

评分公式（单一来源定义在此文件）：
    final_score = alpha * dense_score + (1 - alpha) * sparse_score
                + availability_bonus
                + registry_bonus
                - constraint_penalty

其中：
    - alpha: dense 权重（默认 0.6）
    - dense_score: Dense retrieval 分数（来自 embedding similarity）
    - sparse_score: Sparse retrieval 分数（来自 BM25）
    - availability_bonus: 可用性加分（在线状态）
    - registry_bonus: Registry 状态加分
    - constraint_penalty: 约束惩罚（strict 模式等）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from src.domain.models.hybrid_score import HybridScore
from src.domain.models.retrieval_result import RetrievalProvenance, RetrievalResult
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


@dataclass
class ScoringConfig:
    """评分配置"""
    # Dense/Sparse 权重
    dense_weight: float = 0.6
    sparse_weight: float = 0.4
    
    # Bonus/Penalty
    availability_bonus: float = 0.1  # 在线状态加分
    registry_bonus: float = 0.05     # Registry 状态加分
    strict_penalty: float = 0.0      # strict 模式惩罚（通常为 0）
    
    # 阈值
    min_score: float = 0.0
    max_score: float = 1.0


class RetrievalScorer:
    """
    统一评分器
    
    单一来源：所有 Hybrid Retrieval 的评分公式必须定义在此。
    
    不允许在其他位置复制或修改评分公式。
    """
    
    def __init__(
        self,
        config: Optional[ScoringConfig] = None,
    ):
        """
        初始化评分器
        
        Args:
            config: 评分配置
        """
        self._config = config or ScoringConfig()
    
    def score(
        self,
        dense_score: Optional[float],
        sparse_score: Optional[float],
        is_available: bool = True,
        registry_status: str = "active",
        strict_mode: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ) -> HybridScore:
        """
        计算最终分数
        
        单一来源评分公式：
            final_score = alpha * dense + (1 - alpha) * sparse
                        + availability_bonus
                        + registry_bonus
                        - constraint_penalty
        
        Args:
            dense_score: Dense retrieval 分数
            sparse_score: Sparse retrieval 分数
            is_available: 是否可用（在线状态）
            registry_status: Registry 状态
            strict_mode: 是否 strict 模式
            metadata: 额外元数据
        
        Returns:
            HybridScore: 完整的评分结果
        """
        # 1. 基础分数（dense + sparse 融合）
        base_score = self._compute_base_score(dense_score, sparse_score)
        
        # 2. Bonus
        availability_bonus = self._compute_availability_bonus(is_available)
        registry_bonus = self._compute_registry_bonus(registry_status)
        
        # 3. Penalty
        constraint_penalty = self._compute_constraint_penalty(strict_mode)
        
        # 4. 最终分数
        final_score = base_score + availability_bonus + registry_bonus - constraint_penalty
        
        # 5. 裁剪到有效范围
        final_score = max(
            self._config.min_score,
            min(self._config.max_score, final_score)
        )
        
        # 6. 构建来源信息
        source = self._determine_source(dense_score, sparse_score)
        
        return HybridScore(
            dense_score=dense_score,
            sparse_score=sparse_score,
            dense_weight=self._config.dense_weight,
            sparse_weight=self._config.sparse_weight,
            availability_bonus=availability_bonus,
            registry_bonus=registry_bonus,
            constraint_penalty=constraint_penalty,
            final_score=final_score,
            source=source,
            metadata=metadata or {},
        )
    
    def score_retrieval_result(
        self,
        result: RetrievalResult,
        is_available: bool = True,
        registry_status: str = "active",
        strict_mode: bool = False,
    ) -> RetrievalResult:
        """
        对 RetrievalResult 进行评分
        
        Args:
            result: 检索结果
            is_available: 是否可用
            registry_status: Registry 状态
            strict_mode: 是否 strict 模式
        
        Returns:
            更新了分数的 RetrievalResult
        """
        # 获取 dense/sparse 分数
        dense_score = result.provenance.dense_score
        sparse_score = result.provenance.sparse_score
        
        # 计算分数
        hybrid_score = self.score(
            dense_score=dense_score,
            sparse_score=sparse_score,
            is_available=is_available,
            registry_status=registry_status,
            strict_mode=strict_mode,
            metadata=result.metadata,
        )
        
        # 更新 provenance
        result.provenance.final_score = hybrid_score.final_score
        result.provenance.dense_contribution = (
            dense_score * self._config.dense_weight if dense_score else 0.0
        )
        result.provenance.sparse_contribution = (
            sparse_score * self._config.sparse_weight if sparse_score else 0.0
        )
        
        # 更新 result
        result.score = hybrid_score.final_score
        result.metadata.update(hybrid_score.to_breakdown_dict())
        
        return result
    
    def _compute_base_score(
        self,
        dense_score: Optional[float],
        sparse_score: Optional[float],
    ) -> float:
        """
        计算基础分数（dense + sparse 融合）
        
        单一来源公式：
            base_score = alpha * dense + (1 - alpha) * sparse
        
        当某来源不可用时：
            - dense unavailable: base_score = sparse
            - sparse unavailable: base_score = dense
            - both unavailable: base_score = 0.0
        """
        if dense_score is not None and sparse_score is not None:
            # Hybrid: 融合两者
            return (
                self._config.dense_weight * dense_score
                + self._config.sparse_weight * sparse_score
            )
        elif dense_score is not None:
            # 仅 dense
            return dense_score
        elif sparse_score is not None:
            # 仅 sparse
            return sparse_score
        else:
            # 都不可用
            return 0.0
    
    def _compute_availability_bonus(self, is_available: bool) -> float:
        """计算可用性加分"""
        return self._config.availability_bonus if is_available else 0.0
    
    def _compute_registry_bonus(self, registry_status: str) -> float:
        """计算 Registry 加分"""
        if registry_status in ("active", "online"):
            return self._config.registry_bonus
        return 0.0
    
    def _compute_constraint_penalty(self, strict_mode: bool) -> float:
        """计算约束惩罚"""
        return self._config.strict_penalty if strict_mode else 0.0
    
    def _determine_source(
        self,
        dense_score: Optional[float],
        sparse_score: Optional[float],
    ) -> str:
        """确定评分来源"""
        if dense_score is not None and sparse_score is not None:
            return "hybrid"
        elif dense_score is not None:
            return "dense"
        elif sparse_score is not None:
            return "sparse"
        else:
            return "unknown"


__all__ = [
    "RetrievalScorer",
    "ScoringConfig",
]
