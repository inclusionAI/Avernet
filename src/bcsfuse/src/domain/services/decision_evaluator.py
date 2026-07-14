"""
Decision Evaluator - 决策质量评估器

Phase F: 评估 decision 质量

职责：
- 评估决策来源
- 评估证据质量
- 评估结构化输出完整性
- 评估特殊场景（降级、需要更多信息等）
"""

from __future__ import annotations

import logging
from typing import Any

from src.domain.models.evaluation_result import DecisionQualityMetrics
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


class DecisionEvaluator:
    """
    决策质量评估器

    评估 decision 的质量指标。

    Usage:
        evaluator = DecisionEvaluator()
        metrics = evaluator.evaluate(decision_result)
    """

    def evaluate(
        self,
        decision_result: Any,  # GroupFusionDecision
    ) -> DecisionQualityMetrics:
        """
        评估决策质量

        Args:
            decision_result: 决策结果（GroupFusionDecision）

        Returns:
            DecisionQualityMetrics: 决策质量指标
        """
        if not FeatureFlags.is_enabled("ENABLE_EVALUATION_LOOP"):
            logger.debug("[DecisionEvaluator] Evaluation loop disabled")
            return DecisionQualityMetrics()

        # 提取基本信息
        decision_source = "unknown"

        # 尝试从不同字段获取决策来源
        if hasattr(decision_result, 'source'):
            decision_source = str(decision_result.source)
        elif hasattr(decision_result, 'decision_source'):
            decision_source = str(decision_result.decision_source)
        elif hasattr(decision_result, 'group_id'):
            decision_source = f"group_{decision_result.group_id}"

        # 证据质量
        evidence_count = 0
        high_quality_evidence_ratio = 0.0

        if hasattr(decision_result, 'evidences'):
            evidences = decision_result.evidences or []
            evidence_count = len(evidences)

            if evidences:
                # 计算高质量证据比例（假设有 confidence 字段）
                high_quality_count = sum(
                    1 for e in evidences
                    if hasattr(e, 'confidence') and e.confidence > 0.7
                )
                high_quality_evidence_ratio = high_quality_count / evidence_count

        # 结构化输出完整性
        structured_output_complete = self._check_structured_output(decision_result)

        # 特殊场景
        needs_more_information = False
        degraded_mode = False

        if hasattr(decision_result, 'needs_more_information'):
            needs_more_information = bool(decision_result.needs_more_information)

        if hasattr(decision_result, 'degraded'):
            degraded_mode = bool(decision_result.degraded)
        elif hasattr(decision_result, 'is_degraded'):
            degraded_mode = bool(decision_result.is_degraded)

        # LLM 质量
        llm_call_success = True
        parsing_success = True

        if hasattr(decision_result, 'llm_success'):
            llm_call_success = bool(decision_result.llm_success)

        if hasattr(decision_result, 'parsing_success'):
            parsing_success = bool(decision_result.parsing_success)

        # 构造指标
        metrics = DecisionQualityMetrics(
            decision_source=decision_source,
            evidence_count=evidence_count,
            high_quality_evidence_ratio=high_quality_evidence_ratio,
            structured_output_complete=structured_output_complete,
            needs_more_information=needs_more_information,
            degraded_mode=degraded_mode,
            llm_call_success=llm_call_success,
            parsing_success=parsing_success,
        )

        logger.debug(
            "[DecisionEvaluator] Evaluated decision: source=%s, evidence=%d, degraded=%s",
            decision_source, evidence_count, degraded_mode
        )

        return metrics

    def _check_structured_output(self, decision_result: Any) -> bool:
        """检查结构化输出是否完整"""
        # 检查关键字段是否都存在
        required_fields = []

        # 根据不同类型检查不同字段
        if hasattr(decision_result, 'group_id'):
            group_id = decision_result.group_id
            if group_id == 'G1':
                required_fields = ['recommended_workers']
            elif group_id == 'G2':
                required_fields = ['conflict_detected', 'alignment_strategy']
            elif group_id == 'G5':
                required_fields = ['risk_level', 'risk_factors']

        # 检查字段是否存在且非空
        for field in required_fields:
            if not hasattr(decision_result, field):
                return False
            value = getattr(decision_result, field)
            if value is None or value == [] or value == {}:
                return False

        return True


__all__ = ["DecisionEvaluator"]