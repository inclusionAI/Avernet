"""
Evidence Aggregation Service - 证据聚合服务

Phase D: Unified Evidence Layer

统一 G1/G2/G5 的证据聚合逻辑，提供：
- 证据加权聚合
- 贡献度分析
- 来源分布统计
- 降级链路追踪

设计原则：
- 单一职责：只负责证据聚合
- 可配置：聚合策略可配置
- 可追踪：聚合过程可溯源

约束：
- 这是内部服务，不暴露到API
- Feature Flag 控制是否启用
- 默认禁用，不影响现有行为
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from src.domain.models.evidence import (
    Evidence,
    EvidenceSource,
    EvidenceSourceDistribution,
    EvidenceType,
)
from src.domain.models.evidence_bundle import (
    EvidenceBundle,
    EvidenceContribution,
)
from src.domain.models.fallback_reason_v2 import (
    FallbackReasonV2,
    FallbackReasonCode,
)

logger = logging.getLogger(__name__)


class AggregationConfig:
    """聚合配置"""

    def __init__(
        self,
        normalize: bool = True,
        top_k_contributors: int = 5,
        min_confidence: float = 0.0,
        source_weights: Optional[dict[EvidenceSource, float]] = None,
    ):
        """
        初始化聚合配置

        Args:
            normalize: 是否归一化到[0,1]
            top_k_contributors: 记录的贡献者数量
            min_confidence: 最小置信度阈值
            source_weights: 来源权重覆盖
        """
        self.normalize = normalize
        self.top_k_contributors = top_k_contributors
        self.min_confidence = min_confidence
        self.source_weights = source_weights or {}

        # 默认来源权重（可信度加权）
        self._default_source_weights = {
            EvidenceSource.DENSE_RETRIEVAL: 1.0,
            EvidenceSource.LLM_INFERENCE: 0.9,
            EvidenceSource.SPARSE_RETRIEVAL: 0.85,
            EvidenceSource.TAXONOMY_PRIOR: 0.7,
            EvidenceSource.REGISTRY_STATE: 1.0,
            EvidenceSource.RULE_BASED: 0.6,
            EvidenceSource.EXPLICIT_INPUT: 1.0,
            EvidenceSource.CONSTRAINT_CHECK: 1.0,
        }

    def get_source_weight(self, source: EvidenceSource) -> float:
        """获取来源权重"""
        if source in self.source_weights:
            return self.source_weights[source]
        return self._default_source_weights.get(source, 0.5)


class EvidenceAggregationService:
    """
    证据聚合服务

    统一处理 G1/G2/G5 的证据聚合：
    - 加权聚合
    - 贡献度计算
    - 来源分布统计

    使用示例：
        service = EvidenceAggregationService()

        # 创建 bundle
        bundle = service.create_bundle(
            mode="G5",
            question="诊断数据库性能问题",
        )

        # 添加证据
        bundle = service.add_evidence(bundle, evidence1)
        bundle = service.add_evidences(bundle, [evidence2, evidence3])

        # 聚合
        bundle = service.aggregate(bundle)

        # 获取结果
        summary = service.get_aggregation_summary(bundle)
    """

    def __init__(self, config: Optional[AggregationConfig] = None):
        """
        初始化服务

        Args:
            config: 聚合配置，None 使用默认配置
        """
        self.config = config or AggregationConfig()
        self._fallback_reasons: list[FallbackReasonV2] = []

    def create_bundle(
        self,
        mode: Literal["G1", "G2", "G5"],
        question: str,
        request_id: Optional[str] = None,
        participant_ids: Optional[list[str]] = None,
        strict_participants: bool = False,
    ) -> EvidenceBundle:
        """
        创建证据包

        Args:
            mode: 模式
            question: 问题
            request_id: 请求ID
            participant_ids: 参与者ID列表
            strict_participants: 是否严格参与者模式

        Returns:
            EvidenceBundle: 新创建的证据包
        """
        bundle_id = f"bundle_{mode}_{uuid.uuid4().hex[:8]}"

        bundle = EvidenceBundle(
            bundle_id=bundle_id,
            mode=mode,
            question=question,
            request_id=request_id,
            participant_ids=participant_ids or [],
            strict_participants=strict_participants,
        )

        logger.debug(
            f"[EvidenceAggregation] Created bundle: {bundle_id} for mode={mode}, "
            f"request_id={request_id}, strict_participants={strict_participants}"
        )

        return bundle

    def add_evidence(
        self,
        bundle: EvidenceBundle,
        evidence: Evidence,
    ) -> EvidenceBundle:
        """
        添加单个证据

        Args:
            bundle: 证据包
            evidence: 证据

        Returns:
            EvidenceBundle: 更新后的证据包
        """
        # 应用来源权重调整
        source_weight = self.config.get_source_weight(evidence.source)
        adjusted_weight = evidence.weight * source_weight

        # 创建调整后的证据（保持原始值不变）
        if adjusted_weight != evidence.weight:
            # 创建新证据对象应用调整后的权重
            adjusted_evidence = Evidence(
                evidence_id=evidence.evidence_id,
                evidence_type=evidence.evidence_type,
                source=evidence.source,
                mode=evidence.mode,
                raw_value=evidence.raw_value,
                weight=adjusted_weight,
                weighted_value=evidence.raw_value * adjusted_weight,
                description=evidence.description,
                supporting_facts=evidence.supporting_facts,
                provenance={
                    **evidence.provenance,
                    "original_weight": evidence.weight,
                    "source_weight_adjustment": source_weight,
                },
                confidence=evidence.confidence,
                participant_id=evidence.participant_id,
                created_at=evidence.created_at,
                computation_time_ms=evidence.computation_time_ms,
            )
            bundle.add_evidence(adjusted_evidence)
        else:
            bundle.add_evidence(evidence)

        return bundle

    def add_evidences(
        self,
        bundle: EvidenceBundle,
        evidences: list[Evidence],
    ) -> EvidenceBundle:
        """
        批量添加证据

        Args:
            bundle: 证据包
            evidences: 证据列表

        Returns:
            EvidenceBundle: 更新后的证据包
        """
        for evidence in evidences:
            self.add_evidence(bundle, evidence)
        return bundle

    def aggregate(
        self,
        bundle: EvidenceBundle,
        config: Optional[AggregationConfig] = None,
    ) -> EvidenceBundle:
        """
        聚合证据

        执行加权聚合和贡献度分析。

        Args:
            bundle: 证据包
            config: 可选的聚合配置覆盖

        Returns:
            EvidenceBundle: 聚合后的证据包
        """
        start_time = datetime.now()
        cfg = config or self.config

        # 过滤低置信度证据
        valid_evidences = [
            e for e in bundle.evidences
            if e.confidence >= cfg.min_confidence
        ]

        if not valid_evidences:
            bundle.total_weight = 0.0
            bundle.weighted_sum = 0.0
            bundle.normalized_score = 0.0
            bundle.is_aggregated = True
            bundle.aggregated_at = datetime.now()
            logger.debug(
                f"[EvidenceAggregation] Bundle {bundle.bundle_id}: "
                f"No valid evidences (min_confidence={cfg.min_confidence})"
            )
            return bundle

        # 计算加权和
        bundle.total_weight = sum(e.weight for e in valid_evidences)
        bundle.weighted_sum = sum(e.weighted_value for e in valid_evidences)

        # 归一化
        if cfg.normalize and bundle.total_weight > 0:
            bundle.normalized_score = bundle.weighted_sum / bundle.total_weight
        else:
            bundle.normalized_score = bundle.weighted_sum

        # 计算贡献度
        self._compute_contributions(bundle, cfg.top_k_contributors)

        # 标记完成
        bundle.is_aggregated = True
        bundle.aggregated_at = datetime.now()

        # 计算耗时
        end_time = datetime.now()
        bundle.computation_time_ms = int(
            (end_time - start_time).total_seconds() * 1000
        )

        logger.debug(
            f"[EvidenceAggregation] Bundle {bundle.bundle_id}: "
            f"score={bundle.normalized_score:.4f}, "
            f"evidences={len(valid_evidences)}, "
            f"total_weight={bundle.total_weight:.4f}, "
            f"weighted_sum={bundle.weighted_sum:.4f}, "
            f"top_k={len(bundle.top_contributors)}, "
            f"time={bundle.computation_time_ms}ms"
        )

        return bundle

    def _compute_contributions(
        self,
        bundle: EvidenceBundle,
        top_k: int,
    ) -> None:
        """
        计算贡献度

        Args:
            bundle: 证据包
            top_k: 记录的贡献者数量
        """
        if bundle.weighted_sum == 0:
            bundle.top_contributors = []
            return

        # 按加权值降序排序
        sorted_evidences = sorted(
            bundle.evidences,
            key=lambda e: e.weighted_value,
            reverse=True
        )

        # 计算贡献占比
        contributors = []
        for rank, evidence in enumerate(sorted_evidences[:top_k], 1):
            contribution_ratio = (
                evidence.weighted_value / bundle.weighted_sum
                if bundle.weighted_sum > 0 else 0
            )
            contributors.append(EvidenceContribution(
                evidence_id=evidence.evidence_id,
                contribution_ratio=contribution_ratio,
                rank=rank,
            ))

        bundle.top_contributors = contributors

    def record_fallback(
        self,
        fallback: FallbackReasonV2,
    ) -> None:
        """
        记录降级原因

        Args:
            fallback: 降级原因
        """
        self._fallback_reasons.append(fallback)
        logger.warning(
            f"[EvidenceAggregation] Fallback recorded: "
            f"code={fallback.reason_code.value}, "
            f"mode={fallback.mode}, "
            f"component={fallback.affected_component}"
        )

    def get_fallback_reasons(
        self,
        mode: Optional[Literal["G1", "G2", "G5"]] = None,
    ) -> list[FallbackReasonV2]:
        """
        获取降级原因列表

        Args:
            mode: 可选的模式过滤

        Returns:
            list[FallbackReasonV2]: 降级原因列表
        """
        if mode:
            return [f for f in self._fallback_reasons if f.mode == mode]
        return list(self._fallback_reasons)

    def clear_fallback_reasons(self) -> None:
        """清空降级原因列表"""
        self._fallback_reasons.clear()

    def get_aggregation_summary(
        self,
        bundle: EvidenceBundle,
    ) -> dict[str, Any]:
        """
        获取聚合摘要

        Args:
            bundle: 证据包

        Returns:
            dict: 聚合摘要
        """
        return bundle.get_aggregation_summary()

    def get_evidence_statistics(
        self,
        bundle: EvidenceBundle,
    ) -> dict[str, Any]:
        """
        获取证据统计

        Args:
            bundle: 证据包

        Returns:
            dict: 统计信息
        """
        if not bundle.evidences:
            return {
                "total_count": 0,
                "aggregated": bundle.is_aggregated,
            }

        # 按类型统计
        by_type: dict[str, int] = {}
        for e in bundle.evidences:
            key = e.evidence_type.value
            by_type[key] = by_type.get(key, 0) + 1

        # 按来源统计
        by_source: dict[str, int] = {}
        for e in bundle.evidences:
            key = e.source.value
            by_source[key] = by_source.get(key, 0) + 1

        # 按参与者统计
        by_participant: dict[str, int] = {}
        for e in bundle.evidences:
            if e.participant_id:
                by_participant[e.participant_id] = by_participant.get(e.participant_id, 0) + 1

        # 置信度统计
        confidences = [e.confidence for e in bundle.evidences]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        min_confidence = min(confidences) if confidences else 0
        max_confidence = max(confidences) if confidences else 0

        return {
            "total_count": len(bundle.evidences),
            "aggregated": bundle.is_aggregated,
            "normalized_score": round(bundle.normalized_score, 4),
            "by_type": by_type,
            "by_source": by_source,
            "by_participant": by_participant,
            "confidence": {
                "avg": round(avg_confidence, 4),
                "min": round(min_confidence, 4),
                "max": round(max_confidence, 4),
            },
            "source_distribution": bundle.source_distribution.by_source,
        }

    def merge_bundles(
        self,
        bundles: list[EvidenceBundle],
        target_mode: Optional[Literal["G1", "G2", "G5"]] = None,
    ) -> EvidenceBundle:
        """
        合并多个证据包

        用于跨参与者或跨阶段的证据合并。

        Args:
            bundles: 要合并的证据包列表
            target_mode: 目标模式，None 使用第一个 bundle 的模式

        Returns:
            EvidenceBundle: 合并后的证据包
        """
        if not bundles:
            raise ValueError("Cannot merge empty bundle list")

        if len(bundles) == 1:
            return bundles[0]

        # 确定目标模式
        mode = target_mode or bundles[0].mode

        # 创建新 bundle
        merged = self.create_bundle(
            mode=mode,
            question=bundles[0].question,
            request_id=bundles[0].request_id,
            participant_ids=[
                pid for b in bundles for pid in b.participant_ids
            ],
            strict_participants=any(b.strict_participants for b in bundles),
        )

        # 收集所有证据
        all_evidences = [e for b in bundles for e in b.evidences]
        self.add_evidences(merged, all_evidences)

        # 聚合
        self.aggregate(merged)

        logger.debug(
            f"[EvidenceAggregation] Merged {len(bundles)} bundles into {merged.bundle_id}, "
            f"total_evidences={len(all_evidences)}"
        )

        return merged


# === 全局单例 ===

_evidence_aggregation_service: Optional[EvidenceAggregationService] = None


def get_evidence_aggregation_service() -> EvidenceAggregationService:
    """获取全局服务实例"""
    global _evidence_aggregation_service
    if _evidence_aggregation_service is None:
        _evidence_aggregation_service = EvidenceAggregationService()
    return _evidence_aggregation_service


def reset_evidence_aggregation_service() -> None:
    """重置服务实例（用于测试）"""
    global _evidence_aggregation_service
    _evidence_aggregation_service = None


__all__ = [
    "AggregationConfig",
    "EvidenceAggregationService",
    "get_evidence_aggregation_service",
    "reset_evidence_aggregation_service",
]