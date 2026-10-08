"""
Sample Collector - 样本收集器

Phase F: 沉淀失败/边界样本

职责：
- 识别值得收集的样本
- 创建反馈样本
- 不影响主链路性能
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from src.domain.models.attribution_report import AttributionReport
from src.domain.models.evaluation_result import EvaluationResult
from src.domain.models.feedback_sample import FeedbackSample, SamplePriority, SampleType
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


class SampleCollector:
    """
    样本收集器

    识别并收集失败/边界样本。

    采样策略：
    - 所有失败样本（fallback, degraded）
    - 低质量样本（低分数、少证据）
    - 边界样本（strict constraint, 需要更多信息）
    - 随机样本（用于基线对比）

    Usage:
        collector = SampleCollector()
        sample = collector.should_collect_sample(evaluation_result)
        if sample:
            # 保存样本
            pass
    """

    def __init__(
        self,
        random_sample_rate: float = 0.01,  # 1% 随机采样
    ):
        """
        初始化样本收集器

        Args:
            random_sample_rate: 随机采样率
        """
        self._random_sample_rate = random_sample_rate

    def should_collect_sample(
        self,
        evaluation_result: EvaluationResult,
    ) -> Optional[FeedbackSample]:
        """
        判断是否应该收集样本

        Args:
            evaluation_result: 评估结果

        Returns:
            FeedbackSample: 样本（如果需要收集）
        """
        if not FeatureFlags.is_enabled("ENABLE_SAMPLE_COLLECTION"):
            logger.debug("[SampleCollector] Sample collection disabled")
            return None

        # 判断样本类型
        sample_type, priority = self._determine_sample_type(evaluation_result)

        if sample_type is None:
            return None

        # 创建样本
        sample = self._create_sample(
            sample_type=sample_type,
            priority=priority,
            evaluation_result=evaluation_result,
        )

        logger.info(
            "[SampleCollector] Collecting sample: type=%s, priority=%s, evaluation_id=%s",
            sample_type.value, priority.value, evaluation_result.evaluation_id
        )

        return sample

    def _determine_sample_type(
        self,
        evaluation_result: EvaluationResult,
    ) -> tuple[Optional[SampleType], SamplePriority]:
        """
        确定样本类型和优先级

        Args:
            evaluation_result: 评估结果

        Returns:
            (sample_type, priority): 样本类型和优先级
        """
        # 1. 失败样本（高优先级）
        if evaluation_result.retrieval_metrics.fallback_occurred:
            return SampleType.FAILURE, SamplePriority.HIGH

        if evaluation_result.decision_metrics.degraded_mode:
            return SampleType.FAILURE, SamplePriority.HIGH

        # 2. 降级样本（中优先级）
        if evaluation_result.fallback_attribution:
            return SampleType.FALLBACK, SamplePriority.MEDIUM

        # 3. 低质量样本（中优先级）
        if self._is_low_quality(evaluation_result):
            return SampleType.LOW_QUALITY, SamplePriority.MEDIUM

        # 4. 边界样本（中优先级）
        if evaluation_result.decision_metrics.needs_more_information:
            return SampleType.EDGE_CASE, SamplePriority.MEDIUM

        if evaluation_result.strict_mode and evaluation_result.retrieval_metrics.total_candidates == 0:
            return SampleType.EDGE_CASE, SamplePriority.MEDIUM

        # 5. 随机采样（低优先级）
        import random
        if random.random() < self._random_sample_rate:
            return SampleType.MANUAL, SamplePriority.LOW

        # 不收集
        return None, SamplePriority.LOW

    def _is_low_quality(self, evaluation_result: EvaluationResult) -> bool:
        """判断是否为低质量样本"""
        # 检索质量低
        if evaluation_result.retrieval_metrics.avg_score < 0.3:
            return True

        if evaluation_result.retrieval_metrics.total_candidates == 0:
            return True

        # 决策质量低
        if evaluation_result.decision_metrics.evidence_count == 0:
            return True

        if evaluation_result.decision_metrics.high_quality_evidence_ratio < 0.3:
            return True

        return False

    def _create_sample(
        self,
        sample_type: SampleType,
        priority: SamplePriority,
        evaluation_result: EvaluationResult,
    ) -> FeedbackSample:
        """
        创建样本

        Args:
            sample_type: 样本类型
            priority: 优先级
            evaluation_result: 评估结果

        Returns:
            FeedbackSample: 反馈样本
        """
        sample_id = str(uuid.uuid4())

        return FeedbackSample(
            sample_id=sample_id,
            sample_type=sample_type,
            priority=priority,
            question=evaluation_result.question,
            profile_keys=evaluation_result.profile_keys,
            strict_mode=evaluation_result.strict_mode,
            context={
                "evaluation_id": evaluation_result.evaluation_id,
                "flags_enabled": evaluation_result.flags_enabled,
            },
            retrieval_result=evaluation_result.retrieval_metrics.model_dump() if evaluation_result.retrieval_metrics else None,
            decision_result=evaluation_result.decision_metrics.model_dump() if evaluation_result.decision_metrics else None,
            fallback_attribution=evaluation_result.fallback_attribution,
        )


__all__ = ["SampleCollector"]