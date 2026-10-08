"""
StructuredSignalExtractor - 结构化信号提取器

G2 Conflict Alignment Layer - Phase B

从问题文本和视角中提取结构化立场信号。
"""

from __future__ import annotations

import logging
from typing import Optional

from src.domain.models.fusion_result import Perspective
from src.domain.models.stance_signal import StanceSignal
from src.domain.taxonomy.registry import TaxonomyRegistry, get_taxonomy_registry
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


class StructuredSignalExtractor:
    """
    结构化信号提取器

    从问题和视角中提取立场信号，用于 G2 结构化冲突分析。

    Attributes:
        _registry: 分类体系注册表
    """

    def __init__(self, registry: Optional[TaxonomyRegistry] = None):
        """
        初始化提取器

        Args:
            registry: 分类体系注册表（可选，默认使用全局单例）
        """
        self._registry = registry or get_taxonomy_registry()

    def extract_stance_signals(
        self,
        participant_id: str,
        text: str,
        question: Optional[str] = None,
    ) -> list[StanceSignal]:
        """
        从文本中提取所有维度的立场信号

        Args:
            participant_id: 参与者 ID
            text: 待分析文本（通常是视角摘要 + 关键点 + 顾虑）
            question: 问题文本（可选，用于上下文增强）

        Returns:
            list[StanceSignal]: 立场信号列表
        """
        if not FeatureFlags.is_enabled("ENABLE_G2_STRUCTURED_STANCE"):
            logger.debug(
                "[SignalExtractor] ENABLE_G2_STRUCTURED_STANCE 未开启，返回空信号"
            )
            return []

        signals: list[StanceSignal] = []

        # 合并问题作为上下文
        combined_text = text
        if question:
            combined_text = f"{question} {text}"

        # 遍历所有冲突维度
        dimensions = self._registry.get_conflict_dimensions()
        if not dimensions:
            logger.debug("[SignalExtractor] 无冲突维度配置，返回空信号")
            return []

        for dimension_id, dimension in dimensions.items():
            signal = self._extract_stance_for_dimension(
                participant_id=participant_id,
                text=combined_text,
                dimension_id=dimension_id,
            )
            if signal:
                signals.append(signal)
                logger.debug(
                    "[SignalExtractor] %s 在维度 %s 的立场: %s (强度=%.2f, 置信度=%.2f)",
                    participant_id,
                    dimension_id,
                    signal.position,
                    signal.strength,
                    signal.confidence,
                )

        return signals

    def extract_from_perspective(
        self,
        perspective: Perspective,
        question: Optional[str] = None,
    ) -> list[StanceSignal]:
        """
        从视角对象中提取立场信号

        整合视角的 summary、key_points、concerns 进行分析。

        Args:
            perspective: 视角对象
            question: 问题文本

        Returns:
            list[StanceSignal]: 立场信号列表
        """
        # 合并视角的文本信息
        text_parts = [perspective.summary]

        if perspective.key_points:
            text_parts.extend(perspective.key_points)

        if perspective.concerns:
            text_parts.extend(perspective.concerns)

        if perspective.flexibility:
            text_parts.append(perspective.flexibility)

        combined_text = " ".join(text_parts)

        return self.extract_stance_signals(
            participant_id=perspective.participant_id,
            text=combined_text,
            question=question,
        )

    def _extract_stance_for_dimension(
        self,
        participant_id: str,
        text: str,
        dimension_id: str,
    ) -> Optional[StanceSignal]:
        """
        为指定维度提取立场信号

        Args:
            participant_id: 参与者 ID
            text: 文本
            dimension_id: 维度 ID

        Returns:
            Optional[StanceSignal]: 立场信号，如果无法判定则返回 None
        """
        position, strength, evidence = self._registry.detect_stance_for_dimension(
            text=text,
            dimension_id=dimension_id,
        )

        if position == "unknown":
            return None

        # 计算置信度
        # 基于匹配关键词数量和强度
        confidence = self._calculate_confidence(strength, evidence)

        # 生成判定理由
        dimension = self._registry.get_conflict_dimension(dimension_id)
        rationale = self._generate_rationale(
            dimension_name=dimension.name if dimension else dimension_id,
            position=position,
            evidence=evidence,
        )

        return StanceSignal(
            participant_id=participant_id,
            dimension_id=dimension_id,
            position=position,
            strength=strength,
            confidence=confidence,
            evidence=evidence,
            rationale=rationale,
        )

    def _calculate_confidence(self, strength: float, evidence: list[str]) -> float:
        """
        计算置信度

        基于强度和证据数量综合计算。

        Args:
            strength: 立场强度
            evidence: 证据列表

        Returns:
            float: 置信度 (0.0-1.0)
        """
        # 基础置信度为强度
        base_confidence = strength

        # 证据加成
        evidence_bonus = min(0.3, len(evidence) * 0.1)

        return min(1.0, base_confidence + evidence_bonus)

    def _generate_rationale(
        self,
        dimension_name: str,
        position: str,
        evidence: list[str],
    ) -> str:
        """
        生成判定理由

        Args:
            dimension_name: 维度名称
            position: 立场位置
            evidence: 证据

        Returns:
            str: 判定理由
        """
        position_desc = {
            "axis_a": "倾向正向",
            "axis_b": "倾向反向",
            "balanced": "两端兼顾",
            "neutral": "无明显倾向",
        }

        desc = position_desc.get(position, "无法判断")

        if evidence:
            top_evidence = evidence[:3]
            return f"在「{dimension_name}」维度上{desc}，匹配关键词: {', '.join(top_evidence)}"
        else:
            return f"在「{dimension_name}」维度上{desc}"


__all__ = ["StructuredSignalExtractor"]