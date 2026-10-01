"""
Fallback Attribution - 降级归因

Phase F: 结构化记录失败原因

职责：
- 分析降级原因
- 生成归因报告
- 提供改进建议
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from src.domain.models.attribution_report import (
    AttributionLevel,
    AttributionReport,
    FallbackChain,
    FallbackType,
)
from src.domain.models.evaluation_result import (
    DecisionQualityMetrics,
    RetrievalQualityMetrics,
)
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


class FallbackAttribution:
    """
    降级归因

    分析降级原因并生成归因报告。

    Usage:
        attribution = FallbackAttribution()
        report = attribution.attribute(
            retrieval_result=retrieval_result,
            decision_result=decision_result,
            retrieval_metrics=retrieval_metrics,
            decision_metrics=decision_metrics,
        )
    """

    def attribute(
        self,
        retrieval_result: Any,  # HybridRetrievalResult
        decision_result: Any,  # GroupFusionDecision
        retrieval_metrics: RetrievalQualityMetrics,
        decision_metrics: DecisionQualityMetrics,
    ) -> Optional[AttributionReport]:
        """
        执行降级归因

        Args:
            retrieval_result: 检索结果
            decision_result: 决策结果
            retrieval_metrics: 检索指标
            decision_metrics: 决策指标

        Returns:
            AttributionReport: 归因报告（如果发生降级）
        """
        if not FeatureFlags.is_enabled("ENABLE_FEEDBACK_ATTRIBUTION"):
            logger.debug("[FallbackAttribution] Feedback attribution disabled")
            return None

        # 检查是否发生降级
        if not retrieval_metrics.fallback_occurred and not decision_metrics.degraded_mode:
            logger.debug("[FallbackAttribution] No fallback detected")
            return None

        attribution_id = str(uuid.uuid4())

        # 确定降级类型
        fallback_type = self._determine_fallback_type(
            retrieval_metrics, decision_metrics
        )

        # 构造降级链路
        fallback_chain = self._build_fallback_chain(retrieval_result)

        # 确定归因级别
        level = self._determine_level(fallback_type, retrieval_metrics, decision_metrics)

        # 根因分析
        root_cause = self._analyze_root_cause(
            fallback_type, retrieval_metrics, decision_metrics
        )

        # 贡献因素
        contributing_factors = self._identify_contributing_factors(
            retrieval_metrics, decision_metrics
        )

        # 影响评估
        impact = self._assess_impact(retrieval_metrics, decision_metrics)

        # 改进建议
        recommendations = self._generate_recommendations(fallback_type)

        # 构造归因报告
        report = AttributionReport(
            attribution_id=attribution_id,
            fallback_type=fallback_type,
            level=level,
            description=self._generate_description(fallback_type, root_cause),
            fallback_chain=fallback_chain,
            root_cause=root_cause,
            contributing_factors=contributing_factors,
            impact=impact,
            recommendations=recommendations,
        )

        logger.info(
            "[FallbackAttribution] Created attribution report: type=%s, level=%s",
            fallback_type.value, level.value
        )

        return report

    def _determine_fallback_type(
        self,
        retrieval_metrics: RetrievalQualityMetrics,
        decision_metrics: DecisionQualityMetrics,
    ) -> FallbackType:
        """确定降级类型"""
        # 优先检查 retrieval 降级
        if retrieval_metrics.fallback_reason:
            reason = retrieval_metrics.fallback_reason
            if reason in ["embedding_unavailable", "dense_unavailable"]:
                return FallbackType.DENSE_UNAVAILABLE
            elif reason in ["index_not_ready", "index_missing"]:
                return FallbackType.INDEX_MISSING
            elif reason in ["empty_dense_result"]:
                return FallbackType.SPARSE_FALLBACK
            elif reason in ["strict_constraint"]:
                return FallbackType.STRICT_CONSTRAINT
            elif reason in ["no_matching_candidates"]:
                return FallbackType.NO_MATCHING_CANDIDATES
            elif reason in ["timeout"]:
                return FallbackType.SERVICE_TIMEOUT
            elif reason in ["feature_flag_disabled"]:
                return FallbackType.FEATURE_FLAG_DISABLED

        # 检查 decision 降级
        if decision_metrics.degraded_mode:
            if not decision_metrics.llm_call_success:
                return FallbackType.LLM_DEGRADED
            elif not decision_metrics.parsing_success:
                return FallbackType.PARSING_ERROR

        # 默认
        return FallbackType.SPARSE_FALLBACK

    def _build_fallback_chain(self, retrieval_result: Any) -> FallbackChain:
        """构造降级链路"""
        steps = []
        final_source = "unknown"

        if hasattr(retrieval_result, 'fallback_chain'):
            steps = retrieval_result.fallback_chain or []

        if hasattr(retrieval_result, 'source'):
            final_source = retrieval_result.source.value if hasattr(retrieval_result.source, 'value') else str(retrieval_result.source)

        return FallbackChain(
            steps=steps,
            final_source=final_source,
            fallback_count=len(steps),
        )

    def _determine_level(
        self,
        fallback_type: FallbackType,
        retrieval_metrics: RetrievalQualityMetrics,
        decision_metrics: DecisionQualityMetrics,
    ) -> AttributionLevel:
        """确定归因级别"""
        # 关键问题：阻塞主链路
        if fallback_type in [
            FallbackType.STRICT_CONSTRAINT,
            FallbackType.SERVICE_TIMEOUT,
        ]:
            return AttributionLevel.CRITICAL

        # 警告：降级但可恢复
        if retrieval_metrics.total_candidates == 0 or decision_metrics.degraded_mode:
            return AttributionLevel.WARNING

        # 信息：正常降级
        return AttributionLevel.INFO

    def _analyze_root_cause(
        self,
        fallback_type: FallbackType,
        retrieval_metrics: RetrievalQualityMetrics,
        decision_metrics: DecisionQualityMetrics,
    ) -> str:
        """根因分析"""
        causes = {
            FallbackType.DENSE_UNAVAILABLE: "Dense retrieval service unavailable or disabled",
            FallbackType.INDEX_MISSING: "Profile embedding index not built or empty",
            FallbackType.SPARSE_FALLBACK: "Dense retrieval returned no results",
            FallbackType.STRICT_CONSTRAINT: "Strict mode restricts candidate scope",
            FallbackType.NO_MATCHING_CANDIDATES: "No candidates match query criteria",
            FallbackType.SERVICE_TIMEOUT: "Service timeout exceeded",
            FallbackType.PARSING_ERROR: "Failed to parse LLM response",
            FallbackType.LLM_DEGRADED: "LLM service degraded or unavailable",
            FallbackType.FEATURE_FLAG_DISABLED: "Required feature flag not enabled",
            FallbackType.EMBEDDING_UNAVAILABLE: "Embedding service unavailable",
        }

        return causes.get(fallback_type, "Unknown root cause")

    def _identify_contributing_factors(
        self,
        retrieval_metrics: RetrievalQualityMetrics,
        decision_metrics: DecisionQualityMetrics,
    ) -> list[str]:
        """识别贡献因素"""
        factors = []

        if retrieval_metrics.total_candidates == 0:
            factors.append("Zero candidates retrieved")

        if retrieval_metrics.avg_score < 0.5:
            factors.append("Low average retrieval score")

        if decision_metrics.evidence_count == 0:
            factors.append("No evidence for decision")

        if decision_metrics.needs_more_information:
            factors.append("Decision requires more information")

        if not decision_metrics.structured_output_complete:
            factors.append("Incomplete structured output")

        return factors

    def _assess_impact(
        self,
        retrieval_metrics: RetrievalQualityMetrics,
        decision_metrics: DecisionQualityMetrics,
    ) -> dict[str, Any]:
        """评估影响"""
        return {
            "retrieval_candidates": retrieval_metrics.total_candidates,
            "retrieval_avg_score": retrieval_metrics.avg_score,
            "decision_evidence_count": decision_metrics.evidence_count,
            "decision_degraded": decision_metrics.degraded_mode,
        }

    def _generate_recommendations(self, fallback_type: FallbackType) -> list[str]:
        """生成改进建议"""
        recommendations_map = {
            FallbackType.DENSE_UNAVAILABLE: [
                "Check embedding provider availability",
                "Verify ENABLE_DENSE_RETRIEVAL flag",
                "Check profile embedding index",
            ],
            FallbackType.INDEX_MISSING: [
                "Build profile embedding index",
                "Verify ENABLE_PROFILE_EMBEDDING_INDEX flag",
                "Check embedding provider configuration",
            ],
            FallbackType.SPARSE_FALLBACK: [
                "Review query quality",
                "Consider expanding search scope",
                "Check dense retrieval configuration",
            ],
            FallbackType.STRICT_CONSTRAINT: [
                "Verify participant availability",
                "Consider relaxing strict mode",
                "Review participant selection criteria",
            ],
            FallbackType.NO_MATCHING_CANDIDATES: [
                "Review filtering criteria",
                "Check candidate availability in registry",
                "Consider expanding search parameters",
            ],
            FallbackType.SERVICE_TIMEOUT: [
                "Increase timeout threshold",
                "Optimize service performance",
                "Check service health",
            ],
            FallbackType.PARSING_ERROR: [
                "Review LLM response format",
                "Add parsing fallback logic",
                "Log raw LLM response for debugging",
            ],
            FallbackType.LLM_DEGRADED: [
                "Check LLM provider availability",
                "Add LLM fallback logic",
                "Consider using cached responses",
            ],
            FallbackType.FEATURE_FLAG_DISABLED: [
                "Enable required feature flag",
                "Review feature flag configuration",
            ],
            FallbackType.EMBEDDING_UNAVAILABLE: [
                "Check embedding provider configuration",
                "Verify API credentials",
                "Consider using cached embeddings",
            ],
        }

        return recommendations_map.get(fallback_type, ["Review system logs for details"])

    def _generate_description(
        self,
        fallback_type: FallbackType,
        root_cause: str,
    ) -> str:
        """生成描述"""
        return f"[{fallback_type.value}] {root_cause}"


__all__ = ["FallbackAttribution"]