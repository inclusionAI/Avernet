"""
ConflictDimensionAnalyzer - 冲突维度分析器

G2 Conflict Alignment Layer - Phase B

基于立场信号进行 pairwise 冲突/对齐分析。
"""

from __future__ import annotations

import logging
from typing import Optional

from src.domain.models.stance_signal import StanceSignal
from src.domain.models.structured_conflict_analysis import (
    DimensionSummary,
    PairwiseConflict,
    StructuredConflictAnalysis,
)
from src.domain.taxonomy.registry import TaxonomyRegistry, get_taxonomy_registry
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


class ConflictDimensionAnalyzer:
    """
    冲突维度分析器

    基于立场信号进行 pairwise 冲突/对齐分析，生成结构化输出。

    Attributes:
        _registry: 分类体系注册表
    """

    def __init__(self, registry: Optional[TaxonomyRegistry] = None):
        """
        初始化分析器

        Args:
            registry: 分类体系注册表（可选，默认使用全局单例）
        """
        self._registry = registry or get_taxonomy_registry()

    def analyze(
        self,
        stance_signals: list[StanceSignal],
    ) -> StructuredConflictAnalysis:
        """
        分析立场信号，生成结构化冲突分析结果

        Args:
            stance_signals: 所有参与者的立场信号列表

        Returns:
            StructuredConflictAnalysis: 结构化冲突分析结果
        """
        if not FeatureFlags.is_enabled("ENABLE_G2_CONFLICT_DIMENSIONS"):
            logger.debug(
                "[ConflictAnalyzer] ENABLE_G2_CONFLICT_DIMENSIONS 未开启，返回空分析"
            )
            return StructuredConflictAnalysis()

        if not stance_signals:
            logger.debug("[ConflictAnalyzer] 无立场信号，返回空分析")
            return StructuredConflictAnalysis()

        # 按维度分组立场信号
        signals_by_dimension = self._group_signals_by_dimension(stance_signals)

        # 进行 pairwise 分析
        pairwise_analyses = self._analyze_pairwise(signals_by_dimension)

        # 生成维度摘要
        dimension_summaries = self._generate_dimension_summaries(
            signals_by_dimension, pairwise_analyses
        )

        # 计算整体冲突/对齐程度
        overall_conflict = self._calculate_overall_conflict(pairwise_analyses)
        overall_alignment = self._calculate_overall_alignment(pairwise_analyses)

        # 提取关键冲突和对齐
        key_conflicts = self._extract_key_conflicts(pairwise_analyses)
        key_alignments = self._extract_key_alignments(pairwise_analyses)

        # 生成推荐策略
        recommendation = self._generate_recommendation(
            overall_conflict=overall_conflict,
            overall_alignment=overall_alignment,
            key_conflicts=key_conflicts,
            key_alignments=key_alignments,
        )

        return StructuredConflictAnalysis(
            pairwise_analyses=pairwise_analyses,
            dimension_summaries=dimension_summaries,
            stance_signals=stance_signals,
            overall_conflict_level=overall_conflict,
            overall_alignment_level=overall_alignment,
            key_conflicts=key_conflicts,
            key_alignments=key_alignments,
            recommendation=recommendation,
        )

    def _group_signals_by_dimension(
        self, stance_signals: list[StanceSignal]
    ) -> dict[str, list[StanceSignal]]:
        """
        按维度分组立场信号

        Args:
            stance_signals: 立场信号列表

        Returns:
            dict[str, list[StanceSignal]]: 按维度分组的信号
        """
        grouped: dict[str, list[StanceSignal]] = {}
        for signal in stance_signals:
            if signal.dimension_id not in grouped:
                grouped[signal.dimension_id] = []
            grouped[signal.dimension_id].append(signal)
        return grouped

    def _analyze_pairwise(
        self, signals_by_dimension: dict[str, list[StanceSignal]]
    ) -> list[PairwiseConflict]:
        """
        进行两两分析

        对每个维度内的参与者进行两两比较。
        V2增强：支持动态维度（LLM识别的新维度）

        Args:
            signals_by_dimension: 按维度分组的信号

        Returns:
            list[PairwiseConflict]: 两两分析结果
        """
        analyses: list[PairwiseConflict] = []

        for dimension_id, signals in signals_by_dimension.items():
            # 获取维度定义（优先从registry，否则尝试从信号中获取中文别名）
            dimension = self._registry.get_conflict_dimension(dimension_id)
            if dimension:
                dimension_name = dimension.name
            else:
                # V2增强：尝试从信号中获取维度名称（LLM提取的动态维度）
                dimension_name = self._get_dimension_name_from_signals(signals, dimension_id)

            # 两两比较
            for i in range(len(signals)):
                for j in range(i + 1, len(signals)):
                    signal_a = signals[i]
                    signal_b = signals[j]

                    analysis = self._analyze_pair(
                        signal_a=signal_a,
                        signal_b=signal_b,
                        dimension_id=dimension_id,
                        dimension_name=dimension_name,
                    )
                    analyses.append(analysis)

        return analyses

    def _get_dimension_name_from_signals(
        self, signals: list[StanceSignal], dimension_id: str
    ) -> str:
        """
        从信号中获取维度名称（用于动态维度）

        Args:
            signals: 该维度的信号列表
            dimension_id: 维度ID

        Returns:
            str: 维度名称
        """
        # 尝试从stance_signal中查找是否有存储的维度名称
        # 由于StanceSignal模型没有dimension_name字段，这里使用ID转换
        # 将下划线命名转换为中文格式（如 speed_vs_quality -> 速度与质量）
        name_mapping = {
            "speed_vs_quality": "速度与质量",
            "cost_vs_value": "成本与价值",
            "risk_vs_opportunity": "风险与机会",
            "short_term_vs_long_term": "短期与长期",
            "innovation_vs_stability": "创新与稳定",
            "compliance_vs_business": "合规与业务",
            "quality_vs_speed": "质量与速度",
        }

        if dimension_id in name_mapping:
            return name_mapping[dimension_id]

        # 通用转换：将下划线替换为"与"
        parts = dimension_id.replace("_vs_", "与").split("_")
        if len(parts) >= 2:
            return f"{parts[0]}与{parts[-1]}"

        return dimension_id

    def _analyze_pair(
        self,
        signal_a: StanceSignal,
        signal_b: StanceSignal,
        dimension_id: str,
        dimension_name: str,
    ) -> PairwiseConflict:
        """
        分析两个立场信号之间的关系

        V2增强版判定规则：
        - conflict: 两端对立 (axis_a vs axis_b)，阈值降低
        - alignment: 同端或都是 balanced
        - tension: 一方强，另一方 neutral/balanced
        - none: 双方都是 neutral/unknown

        V2增强：
        1. 降低阈值: conflict_threshold 从 0.6 降为 0.4
        2. 单方检测: 一方强度>=0.6且双方向立即可判conflict
        3. 双方检测: 双方都>=0.4也可判conflict

        Args:
            signal_a: 立场信号 A
            signal_b: 立场信号 B
            dimension_id: 维度 ID
            dimension_name: 维度名称

        Returns:
            PairwiseConflict: 分析结果
        """
        thresholds = self._registry.get_conflict_dimension_thresholds()
        # V2增强: 降低阈值从0.6到0.4，提高冲突检测灵敏度
        conflict_threshold = thresholds.get("conflict_strength_threshold", 0.4)
        alignment_threshold = thresholds.get("alignment_strength_threshold", 0.3)

        position_a = signal_a.position
        position_b = signal_b.position
        strength_a = signal_a.strength
        strength_b = signal_b.strength
        confidence_a = signal_a.confidence
        confidence_b = signal_b.confidence

        # 判定冲突类型
        conflict_type = "none"
        severity = None
        rationale = None
        evidence = list(set(signal_a.evidence + signal_b.evidence))

        # 计算 combined confidence
        combined_confidence = (confidence_a + confidence_b) / 2
        avg_strength = (strength_a + strength_b) / 2

        # Case 1: 双方都是 neutral/unknown
        if position_a in ("neutral", "unknown") and position_b in ("neutral", "unknown"):
            conflict_type = "none"
            rationale = f"双方在「{dimension_name}」维度上均无明显倾向"

        # Case 2: 对立冲突 (axis_a vs axis_b) - V2增强判定逻辑
        elif (position_a == "axis_a" and position_b == "axis_b") or \
             (position_a == "axis_b" and position_b == "axis_a"):
            max_strength = max(strength_a, strength_b)
            min_strength = min(strength_a, strength_b)

            # V2增强判定逻辑:
            # 1. 单方高强度检测: max >= 0.6 且 combined_confidence >= 0.5
            # 2. 双方中等强度检测: min >= 0.4 且 combined_confidence >= 0.4
            is_conflict = (
                (max_strength >= 0.6 and combined_confidence >= 0.5) or
                (min_strength >= conflict_threshold and combined_confidence >= 0.4)
            )

            if is_conflict:
                conflict_type = "conflict"
                # 根据平均强度和置信度判定严重程度
                if avg_strength >= 0.8 and combined_confidence >= 0.7:
                    severity = "high"
                elif avg_strength >= 0.6:
                    severity = "medium"
                else:
                    severity = "low"
                rationale = f"双方在「{dimension_name}」维度上立场对立: 分别倾向{dimension_name}的两个极端"
            else:
                # 即使不完全满足冲突判定，如果有明确对立立场也记录为tension
                conflict_type = "tension"
                severity = "low"
                rationale = f"双方在「{dimension_name}」维度上存在对立倾向，强度为{avg_strength:.2f}"

        # Case 3: 同端对齐
        elif position_a == position_b and position_a not in ("neutral", "unknown"):
            conflict_type = "alignment"
            rationale = f"双方在「{dimension_name}」维度上立场一致，共同倾向{position_a}"

        # Case 4: balanced 与端点
        elif position_a == "balanced" or position_b == "balanced":
            other_position = position_b if position_a == "balanced" else position_a
            other_signal = signal_b if position_a == "balanced" else signal_a
            if other_position in ("neutral", "unknown"):
                conflict_type = "none"
                rationale = f"一方平衡，一方无明显倾向"
            else:
                # balanced 与有明确倾向的一方存在张力
                conflict_type = "tension"
                severity = "low"
                rationale = f"一方在「{dimension_name}」维度上平衡，另一方倾向{other_position}"

        # Case 5: 一方有明显倾向，一方 neutral
        elif position_a in ("neutral", "unknown") or position_b in ("neutral", "unknown"):
            conflict_type = "tension"
            severity = "low"
            strong_position = position_a if position_b in ("neutral", "unknown") else position_b
            rationale = f"一方在「{dimension_name}」维度上倾向{strong_position}，另一方无明显倾向"

        # Default
        else:
            conflict_type = "none"
            rationale = f"无法判断双方在「{dimension_name}」维度上的关系"

        return PairwiseConflict(
            participant_a=signal_a.participant_id,
            participant_b=signal_b.participant_id,
            dimension_id=dimension_id,
            conflict_type=conflict_type,
            stance_a=signal_a,
            stance_b=signal_b,
            severity=severity,
            confidence=combined_confidence,
            evidence=evidence[:5],  # 最多保留5条证据
            rationale=rationale,
        )

    def _generate_dimension_summaries(
        self,
        signals_by_dimension: dict[str, list[StanceSignal]],
        pairwise_analyses: list[PairwiseConflict],
    ) -> list[DimensionSummary]:
        """
        生成维度摘要

        Args:
            signals_by_dimension: 按维度分组的信号
            pairwise_analyses: 两两分析结果

        Returns:
            list[DimensionSummary]: 维度摘要列表
        """
        summaries: list[DimensionSummary] = []

        for dimension_id, signals in signals_by_dimension.items():
            dimension = self._registry.get_conflict_dimension(dimension_id)
            dimension_name = dimension.name if dimension else dimension_id

            # 统计该维度的分析结果
            dimension_analyses = [
                pa for pa in pairwise_analyses if pa.dimension_id == dimension_id
            ]

            conflict_count = sum(1 for pa in dimension_analyses if pa.conflict_type == "conflict")
            alignment_count = sum(1 for pa in dimension_analyses if pa.conflict_type == "alignment")
            tension_count = sum(1 for pa in dimension_analyses if pa.conflict_type == "tension")

            # 判断主导立场
            position_counts: dict[str, int] = {}
            for signal in signals:
                if signal.position not in ("neutral", "unknown"):
                    position_counts[signal.position] = position_counts.get(signal.position, 0) + 1

            dominant_position = None
            if position_counts:
                max_count = max(position_counts.values())
                if max_count > len(signals) // 2:
                    dominant_position = max(position_counts, key=position_counts.get)

            # 收集参与者
            participants = list(set(s.participant_id for s in signals))

            summaries.append(DimensionSummary(
                dimension_id=dimension_id,
                dimension_name=dimension_name,
                conflict_count=conflict_count,
                alignment_count=alignment_count,
                tension_count=tension_count,
                dominant_position=dominant_position,
                participants=participants,
            ))

        return summaries

    def _calculate_overall_conflict(
        self, pairwise_analyses: list[PairwiseConflict]
    ) -> str:
        """
        计算整体冲突程度

        Args:
            pairwise_analyses: 两两分析结果

        Returns:
            str: 冲突程度
        """
        if not pairwise_analyses:
            return "none"

        conflict_count = sum(1 for pa in pairwise_analyses if pa.conflict_type == "conflict")
        high_severity_count = sum(
            1 for pa in pairwise_analyses
            if pa.conflict_type == "conflict" and pa.severity in ("high", "critical")
        )
        total_count = len(pairwise_analyses)

        if high_severity_count >= 2:
            return "critical"
        elif high_severity_count >= 1:
            return "high"
        elif conflict_count >= 3:
            return "high"
        elif conflict_count >= 2:
            return "medium"
        elif conflict_count >= 1:
            return "low"
        else:
            return "none"

    def _calculate_overall_alignment(
        self, pairwise_analyses: list[PairwiseConflict]
    ) -> str:
        """
        计算整体对齐程度

        Args:
            pairwise_analyses: 两两分析结果

        Returns:
            str: 对齐程度
        """
        if not pairwise_analyses:
            return "none"

        alignment_count = sum(1 for pa in pairwise_analyses if pa.conflict_type == "alignment")
        total_count = len(pairwise_analyses)

        ratio = alignment_count / total_count if total_count > 0 else 0

        if ratio >= 0.7:
            return "high"
        elif ratio >= 0.5:
            return "medium"
        elif ratio >= 0.3:
            return "low"
        else:
            return "none"

    def _extract_key_conflicts(
        self, pairwise_analyses: list[PairwiseConflict]
    ) -> list[PairwiseConflict]:
        """
        提取关键冲突

        按严重程度和置信度排序，返回前 3 个。

        Args:
            pairwise_analyses: 两两分析结果

        Returns:
            list[PairwiseConflict]: 关键冲突列表
        """
        conflicts = [pa for pa in pairwise_analyses if pa.conflict_type == "conflict"]

        # 按严重程度和置信度排序
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sorted_conflicts = sorted(
            conflicts,
            key=lambda x: (severity_order.get(x.severity or "low", 4), -x.confidence),
        )

        return sorted_conflicts[:3]

    def _extract_key_alignments(
        self, pairwise_analyses: list[PairwiseConflict]
    ) -> list[PairwiseConflict]:
        """
        提取关键对齐

        按置信度排序，返回前 3 个。

        Args:
            pairwise_analyses: 两两分析结果

        Returns:
            list[PairwiseConflict]: 关键对齐列表
        """
        alignments = [pa for pa in pairwise_analyses if pa.conflict_type == "alignment"]

        # 按置信度排序
        sorted_alignments = sorted(alignments, key=lambda x: -x.confidence)

        return sorted_alignments[:3]

    def _generate_recommendation(
        self,
        overall_conflict: str,
        overall_alignment: str,
        key_conflicts: list[PairwiseConflict],
        key_alignments: list[PairwiseConflict],
    ) -> Optional[str]:
        """
        生成推荐策略

        Args:
            overall_conflict: 整体冲突程度
            overall_alignment: 整体对齐程度
            key_conflicts: 关键冲突
            key_alignments: 关键对齐

        Returns:
            Optional[str]: 推荐策略
        """
        if overall_conflict == "critical":
            return "存在严重冲突，建议暂停推进并优先协调分歧"
        elif overall_conflict == "high":
            conflict_dims = list(set(c.dimension_id for c in key_conflicts))
            dimensions_str = "、".join(conflict_dims[:2])
            return f"在「{dimensions_str}」等维度存在明显分歧，建议优先协调"
        elif overall_conflict == "medium":
            if key_alignments:
                return "存在部分分歧，建议基于共识点推进协调"
            else:
                return "存在分歧，建议进一步沟通协商"
        elif overall_alignment in ("high", "medium"):
            return "立场基本一致，建议推进实施"
        else:
            return "需要更多信息以形成判断"


__all__ = ["ConflictDimensionAnalyzer"]