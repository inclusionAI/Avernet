"""
Retrieval Evaluator - 检索质量评估器

Phase F: 评估 retrieval 质量

职责：
- 评估召回数量
- 评估分数分布
- 评估召回来源
- 评估 fallback 情况
"""

from __future__ import annotations

import logging
from typing import Any

from src.domain.models.evaluation_result import RetrievalQualityMetrics
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


class RetrievalEvaluator:
    """
    检索质量评估器

    评估 retrieval 的质量指标。

    Usage:
        evaluator = RetrievalEvaluator()
        metrics = evaluator.evaluate(retrieval_result)
    """

    def evaluate(
        self,
        retrieval_result: Any,  # HybridRetrievalResult
    ) -> RetrievalQualityMetrics:
        """
        评估检索质量

        Args:
            retrieval_result: 检索结果（HybridRetrievalResult）

        Returns:
            RetrievalQualityMetrics: 检索质量指标
        """
        if not FeatureFlags.is_enabled("ENABLE_EVALUATION_LOOP"):
            logger.debug("[RetrievalEvaluator] Evaluation loop disabled")
            return RetrievalQualityMetrics()

        # 提取基本信息
        candidates = getattr(retrieval_result, 'candidates', [])
        total_candidates = len(candidates)

        # 候选数量
        dense_candidates = getattr(retrieval_result, 'dense_candidates', 0)
        sparse_candidates = getattr(retrieval_result, 'sparse_candidates', 0)

        # 召回来源
        source = getattr(retrieval_result, 'source', 'unknown')
        if hasattr(source, 'value'):
            source = source.value

        # Fallback 信息
        fallback_occurred = getattr(retrieval_result, 'fallback_occurred', False)
        fallback_reason = getattr(retrieval_result, 'fallback_reason', None)
        if fallback_reason and hasattr(fallback_reason, 'value'):
            fallback_reason = fallback_reason.value

        # 分数分布
        scores = [c.score for c in candidates if hasattr(c, 'score')]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        max_score = max(scores) if scores else 0.0
        min_score = min(scores) if scores else 0.0

        # 构造指标
        metrics = RetrievalQualityMetrics(
            total_candidates=total_candidates,
            dense_candidates=dense_candidates,
            sparse_candidates=sparse_candidates,
            avg_score=avg_score,
            max_score=max_score,
            min_score=min_score,
            source=source,
            fallback_occurred=fallback_occurred,
            fallback_reason=fallback_reason,
        )

        logger.debug(
            "[RetrievalEvaluator] Evaluated retrieval: source=%s, candidates=%d, fallback=%s",
            source, total_candidates, fallback_occurred
        )

        return metrics


__all__ = ["RetrievalEvaluator"]