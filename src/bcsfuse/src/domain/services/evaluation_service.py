"""
Evaluation Service - 评估服务

Phase F: 统一评估入口

职责：
- 协调 retrieval 和 decision 评估
- 聚合评估结果
- 支持 fallback attribution
- 支持 sample collection
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from src.domain.models.attribution_report import AttributionReport
from src.domain.models.evaluation_result import (
    DecisionQualityMetrics,
    EvaluationResult,
    RetrievalQualityMetrics,
)
from src.domain.models.feedback_sample import FeedbackSample, SamplePriority, SampleType
from src.domain.services.decision_evaluator import DecisionEvaluator
from src.domain.services.fallback_attribution import FallbackAttribution
from src.domain.services.retrieval_evaluator import RetrievalEvaluator
from src.domain.services.sample_collector import SampleCollector
from src.infra.config.feature_flags import FeatureFlags
from src.infra.storage.feedback_store import FeedbackStore

logger = logging.getLogger(__name__)


class EvaluationService:
    """
    评估服务

    统一评估入口，协调 retrieval 和 decision 评估。

    Usage:
        service = EvaluationService(
            retrieval_evaluator=retrieval_evaluator,
            decision_evaluator=decision_evaluator,
            fallback_attribution=fallback_attribution,
            sample_collector=sample_collector,
            feedback_store=feedback_store,
        )

        result = service.evaluate(
            question=question,
            retrieval_result=retrieval_result,
            decision_result=decision_result,
        )
    """

    def __init__(
        self,
        retrieval_evaluator: RetrievalEvaluator,
        decision_evaluator: DecisionEvaluator,
        fallback_attribution: FallbackAttribution,
        sample_collector: SampleCollector,
        feedback_store: Optional[FeedbackStore] = None,
    ):
        """
        初始化评估服务

        Args:
            retrieval_evaluator: 检索评估器
            decision_evaluator: 决策评估器
            fallback_attribution: 降级归因
            sample_collector: 样本收集器
            feedback_store: 反馈存储
        """
        self._retrieval_evaluator = retrieval_evaluator
        self._decision_evaluator = decision_evaluator
        self._fallback_attribution = fallback_attribution
        self._sample_collector = sample_collector
        self._feedback_store = feedback_store

    def evaluate(
        self,
        question: str,
        retrieval_result: Any,  # HybridRetrievalResult
        decision_result: Any,  # GroupFusionDecision
        profile_keys: Optional[list[str]] = None,
        strict_mode: bool = False,
        flags_enabled: Optional[list[str]] = None,
    ) -> EvaluationResult:
        """
        执行完整评估

        Args:
            question: 问题文本
            retrieval_result: 检索结果
            decision_result: 决策结果
            profile_keys: 显式 participants
            strict_mode: 是否 strict 模式
            flags_enabled: 启用的 feature flags

        Returns:
            EvaluationResult: 评估结果
        """
        if not FeatureFlags.is_enabled("ENABLE_EVALUATION_LOOP"):
            logger.debug("[EvaluationService] Evaluation loop disabled by feature flag")
            return self._create_empty_result(question)

        evaluation_id = str(uuid.uuid4())
        logger.info(
            "[EvaluationService] Starting evaluation: evaluation_id=%s, question='%s'",
            evaluation_id, question[:50]
        )

        # 1. Retrieval 评估
        retrieval_metrics = self._retrieval_evaluator.evaluate(retrieval_result)

        # 2. Decision 评估
        decision_metrics = self._decision_evaluator.evaluate(decision_result)

        # 3. Fallback attribution
        fallback_attribution_report = None
        if FeatureFlags.is_enabled("ENABLE_FEEDBACK_ATTRIBUTION"):
            if retrieval_metrics.fallback_occurred or decision_metrics.degraded_mode:
                fallback_attribution_report = self._fallback_attribution.attribute(
                    retrieval_result=retrieval_result,
                    decision_result=decision_result,
                    retrieval_metrics=retrieval_metrics,
                    decision_metrics=decision_metrics,
                )

        # 4. 构造评估结果
        result = EvaluationResult(
            evaluation_id=evaluation_id,
            question=question,
            profile_keys=profile_keys,
            strict_mode=strict_mode,
            retrieval_metrics=retrieval_metrics,
            decision_metrics=decision_metrics,
            fallback_attribution=fallback_attribution_report.model_dump() if fallback_attribution_report else None,
            flags_enabled=flags_enabled or [],
        )

        # 5. Sample collection
        if FeatureFlags.is_enabled("ENABLE_SAMPLE_COLLECTION"):
            sample = self._sample_collector.should_collect_sample(
                evaluation_result=result,
            )
            if sample:
                result.is_sample = True
                result.sample_reason = sample.sample_type.value
                self._save_sample(sample)

        # 6. 持久化评估结果
        if self._feedback_store:
            self._feedback_store.save_evaluation_result(result)

        logger.info(
            "[EvaluationService] Evaluation completed: evaluation_id=%s, retrieval_source=%s, decision_source=%s",
            evaluation_id, retrieval_metrics.source, decision_metrics.decision_source
        )

        return result

    def _create_empty_result(self, question: str) -> EvaluationResult:
        """创建空评估结果"""
        return EvaluationResult(
            evaluation_id=str(uuid.uuid4()),
            question=question,
        )

    def _save_sample(self, sample: FeedbackSample) -> None:
        """保存样本"""
        if self._feedback_store:
            self._feedback_store.save_feedback_sample(sample)
            logger.info("[EvaluationService] Saved feedback sample: %s", sample.sample_id)


__all__ = ["EvaluationService"]