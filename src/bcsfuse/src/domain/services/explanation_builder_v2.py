"""
Explanation Builder V2 - 解释构建服务

Phase D: Unified Evidence Layer

基于统一 Evidence 模型构建人类可读的解释。

设计原则：
- 证据驱动：解释完全基于 EvidenceBundle
- 模式感知：针对 G1/G2/G5 生成不同风格的解释
- 可溯源：解释中包含来源信息

约束：
- 内部服务，不暴露到API
- Feature Flag 控制是否启用
- 默认禁用，不影响现有行为
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from src.domain.models.evidence_bundle import EvidenceBundle
from src.domain.models.evidence import Evidence, EvidenceSource, EvidenceType

logger = logging.getLogger(__name__)


class ExplanationStyle:
    """解释风格枚举"""

    DETAILED = "detailed"      # 详细版（含来源、权重）
    CONCISE = "concise"        # 简洁版（关键因素）
    TECHNICAL = "technical"    # 技术版（含分数明细）
    USER_FRIENDLY = "user_friendly"  # 用户友好版（自然语言）


class ExplanationBuilderV2:
    """
    解释构建服务 V2

    基于 EvidenceBundle 构建解释，支持多种输出风格。

    使用示例：
        builder = ExplanationBuilderV2()

        # 构建解释
        explanation = builder.build(
            bundle=evidence_bundle,
            style="concise",
        )

        # 获取摘要
        summary = builder.build_summary(bundle)
    """

    # 模式特定的解释模板
    MODE_TEMPLATES = {
        "G1": {
            "summary": "推荐结果基于 {evidence_count} 个评估因素，综合得分 {score:.2%}",
            "top_factor": "主要因素: {factors}",
            "source_note": "数据来源: {sources}",
        },
        "G2": {
            "summary": "冲突分析基于 {evidence_count} 个立场信号，检测到 {conflict_count} 个潜在冲突",
            "alignment": "对齐点: {alignments}",
            "conflict": "冲突点: {conflicts}",
        },
        "G5": {
            "summary": "风险评估识别出 {risk_count} 个风险因素，整体风险等级: {risk_level}",
            "critical": "关键风险: {critical_risks}",
            "recommendation": "建议: {recommendations}",
        },
    }

    # EvidenceType 到中文描述的映射
    TYPE_LABELS = {
        EvidenceType.SKILL_MATCH: "技能匹配",
        EvidenceType.CAPABILITY_COVERAGE: "能力覆盖",
        EvidenceType.SEMANTIC_SIMILARITY: "语义相似度",
        EvidenceType.AVAILABILITY: "可用性",
        EvidenceType.DOMAIN_EXPERTISE: "领域专长",
        EvidenceType.STANCE: "立场",
        EvidenceType.CONFLICT_INDICATOR: "冲突指示",
        EvidenceType.ALIGNMENT_INDICATOR: "对齐指示",
        EvidenceType.RISK_FACTOR: "风险因素",
        EvidenceType.SCENARIO_MATCH: "场景匹配",
        EvidenceType.REGISTRY_STATE: "注册状态",
        EvidenceType.EXPLICIT_INPUT: "显式输入",
        EvidenceType.CONSTRAINT_VIOLATION: "约束违规",
    }

    # EvidenceSource 到可信度描述的映射
    SOURCE_LABELS = {
        EvidenceSource.DENSE_RETRIEVAL: "向量检索",
        EvidenceSource.LLM_INFERENCE: "LLM推断",
        EvidenceSource.SPARSE_RETRIEVAL: "关键词匹配",
        EvidenceSource.TAXONOMY_PRIOR: "领域先验",
        EvidenceSource.REGISTRY_STATE: "系统状态",
        EvidenceSource.RULE_BASED: "规则计算",
        EvidenceSource.EXPLICIT_INPUT: "用户输入",
        EvidenceSource.CONSTRAINT_CHECK: "约束检查",
    }

    def __init__(self):
        """初始化构建器"""
        self._explanation_cache: dict[str, str] = {}

    def build(
        self,
        bundle: EvidenceBundle,
        style: Literal["detailed", "concise", "technical", "user_friendly"] = "concise",
        max_factors: int = 5,
        include_source: bool = True,
    ) -> str:
        """
        构建解释

        Args:
            bundle: 证据包
            style: 解释风格
            max_factors: 最大显示因素数
            include_source: 是否包含来源信息

        Returns:
            str: 构建的解释文本
        """
        if not bundle.is_aggregated:
            logger.warning(
                f"[ExplanationBuilder] Bundle {bundle.bundle_id} not aggregated, "
                "aggregating first..."
            )
            bundle.aggregate()

        if style == ExplanationStyle.DETAILED:
            return self._build_detailed(bundle, max_factors, include_source)
        elif style == ExplanationStyle.TECHNICAL:
            return self._build_technical(bundle, max_factors)
        elif style == ExplanationStyle.USER_FRIENDLY:
            return self._build_user_friendly(bundle, max_factors)
        else:
            return self._build_concise(bundle, max_factors)

    def _build_concise(
        self,
        bundle: EvidenceBundle,
        max_factors: int,
    ) -> str:
        """构建简洁版解释"""
        if not bundle.evidences:
            return "无可用证据支持决策。"

        # 获取模式模板
        templates = self.MODE_TEMPLATES.get(bundle.mode, self.MODE_TEMPLATES["G1"])

        # 构建因素列表
        top_contributors = bundle.get_top_k_contributors(max_factors)
        factors = []
        for contrib in top_contributors:
            evidence = bundle.get_evidence_by_id(contrib.evidence_id)
            if evidence:
                type_label = self.TYPE_LABELS.get(
                    evidence.evidence_type,
                    evidence.evidence_type.value
                )
                factors.append(
                    f"{type_label}({contrib.contribution_ratio:.0%})"
                )

        # 格式化基本摘要
        if bundle.mode == "G1":
            summary = templates["summary"].format(
                evidence_count=len(bundle.evidences),
                score=bundle.normalized_score,
            )
            if factors:
                summary += f"\n{templates['top_factor'].format(factors=', '.join(factors))}"
        elif bundle.mode == "G2":
            # G2: 计算冲突和对齐点
            conflict_count = len([
                e for e in bundle.evidences
                if e.evidence_type == EvidenceType.CONFLICT_INDICATOR
            ])
            alignment_count = len([
                e for e in bundle.evidences
                if e.evidence_type == EvidenceType.ALIGNMENT_INDICATOR
            ])

            summary = templates["summary"].format(
                evidence_count=len(bundle.evidences),
                conflict_count=conflict_count,
            )
            if alignment_count > 0:
                summary += f"\n发现 {alignment_count} 个对齐点"
        elif bundle.mode == "G5":
            # G5: 获取风险因素数量
            risk_count = len([
                e for e in bundle.evidences
                if e.evidence_type == EvidenceType.RISK_FACTOR
            ])

            # 推断风险等级
            if bundle.normalized_score >= 0.75:
                risk_level = "高"
            elif bundle.normalized_score >= 0.5:
                risk_level = "中"
            else:
                risk_level = "低"

            summary = templates["summary"].format(
                risk_count=risk_count,
                risk_level=risk_level,
            )
        else:
            summary = f"基于 {len(bundle.evidences)} 个证据，综合得分: {bundle.normalized_score:.2%}"

        return summary

    def _build_detailed(
        self,
        bundle: EvidenceBundle,
        max_factors: int,
        include_source: bool,
    ) -> str:
        """构建详细版解释"""
        lines = []

        # 标题
        lines.append(f"=== {bundle.mode} 证据解释 ===")
        lines.append(f"问题: {bundle.question}")
        lines.append("")

        # 聚合摘要
        lines.append("【聚合摘要】")
        lines.append(f"  证据总数: {len(bundle.evidences)}")
        lines.append(f"  综合得分: {bundle.normalized_score:.4f}")
        lines.append(f"  加权总和: {bundle.weighted_sum:.4f}")
        lines.append("")

        # 主要贡献因素
        lines.append("【主要贡献因素】")
        top_contributors = bundle.get_top_k_contributors(max_factors)
        for contrib in top_contributors:
            evidence = bundle.get_evidence_by_id(contrib.evidence_id)
            if evidence:
                type_label = self.TYPE_LABELS.get(
                    evidence.evidence_type,
                    evidence.evidence_type.value
                )
                source_label = self.SOURCE_LABELS.get(
                    evidence.source,
                    evidence.source.value
                )

                factor_line = (
                    f"  #{contrib.rank} {type_label}: "
                    f"贡献度 {contrib.contribution_ratio:.1%}, "
                    f"得分 {evidence.raw_value:.2f}"
                )
                if include_source:
                    factor_line += f", 来源: {source_label}"
                lines.append(factor_line)

                # 添加支持事实
                for fact in evidence.supporting_facts[:2]:
                    lines.append(f"      - {fact}")

        # 来源分布
        if include_source and bundle.source_distribution.total_count > 0:
            lines.append("")
            lines.append("【来源分布】")
            for source, count in bundle.source_distribution.by_source.items():
                source_label = self.SOURCE_LABELS.get(
                    EvidenceSource(source),
                    source
                )
                lines.append(f"  {source_label}: {count}")

        return "\n".join(lines)

    def _build_technical(
        self,
        bundle: EvidenceBundle,
        max_factors: int,
    ) -> str:
        """构建技术版解释"""
        lines = []

        lines.append(f"mode: {bundle.mode}")
        lines.append(f"score: {bundle.normalized_score:.6f}")
        lines.append(f"weighted_sum: {bundle.weighted_sum:.6f}")
        lines.append(f"total_weight: {bundle.total_weight:.6f}")
        lines.append(f"evidence_count: {len(bundle.evidences)}")
        lines.append("")

        lines.append("top_contributors:")
        top_contributors = bundle.get_top_k_contributors(max_factors)
        for contrib in top_contributors:
            evidence = bundle.get_evidence_by_id(contrib.evidence_id)
            if evidence:
                lines.append(f"  - id: {contrib.evidence_id}")
                lines.append(f"    type: {evidence.evidence_type.value}")
                lines.append(f"    source: {evidence.source.value}")
                lines.append(f"    raw_value: {evidence.raw_value:.6f}")
                lines.append(f"    weight: {evidence.weight:.6f}")
                lines.append(f"    contribution: {contrib.contribution_ratio:.6f}")

        lines.append("")
        lines.append("source_distribution:")
        for source, count in bundle.source_distribution.by_source.items():
            lines.append(f"  {source}: {count}")

        return "\n".join(lines)

    def _build_user_friendly(
        self,
        bundle: EvidenceBundle,
        max_factors: int,
    ) -> str:
        """构建用户友好版解释"""
        if not bundle.evidences:
            return "目前没有足够的信息来提供建议。"

        if bundle.mode == "G1":
            # G1: 推荐场景
            if bundle.normalized_score >= 0.8:
                confidence = "高度推荐"
            elif bundle.normalized_score >= 0.6:
                confidence = "推荐"
            elif bundle.normalized_score >= 0.4:
                confidence = "可能适合"
            else:
                confidence = "不太推荐"

            top_evidence = None
            top_contributors = bundle.get_top_k_contributors(1)
            if top_contributors:
                top_evidence = bundle.get_evidence_by_id(top_contributors[0].evidence_id)

            explanation = f"{confidence}此选项。"
            if top_evidence:
                type_label = self.TYPE_LABELS.get(
                    top_evidence.evidence_type,
                    top_evidence.evidence_type.value
                )
                explanation += f" 主要因为{type_label}表现较好。"

            return explanation

        elif bundle.mode == "G2":
            # G2: 冲突分析场景
            conflict_evidences = [
                e for e in bundle.evidences
                if e.evidence_type == EvidenceType.CONFLICT_INDICATOR
            ]
            alignment_evidences = [
                e for e in bundle.evidences
                if e.evidence_type == EvidenceType.ALIGNMENT_INDICATOR
            ]

            if conflict_evidences:
                return (
                    f"检测到 {len(conflict_evidences)} 处观点分歧，"
                    f"同时发现 {len(alignment_evidences)} 处共识。"
                    "建议进一步讨论分歧点。"
                )
            else:
                return "各方观点基本一致，未发现明显冲突。"

        elif bundle.mode == "G5":
            # G5: 风险评估场景
            risk_evidences = [
                e for e in bundle.evidences
                if e.evidence_type == EvidenceType.RISK_FACTOR
            ]

            if bundle.normalized_score >= 0.75:
                level = "较高"
                action = "建议谨慎评估后再做决策"
            elif bundle.normalized_score >= 0.5:
                level = "中等"
                action = "存在一定风险，需要关注"
            else:
                level = "较低"
                action = "风险可控"

            return (
                f"风险等级评估为{level}。"
                f"共识别出 {len(risk_evidences)} 个风险因素。"
                f"{action}。"
            )

        else:
            return f"基于分析，综合评分: {bundle.normalized_score:.0%}"

    def build_summary(
        self,
        bundle: EvidenceBundle,
    ) -> str:
        """
        构建简短摘要

        一句话总结证据包的核心结论。

        Args:
            bundle: 证据包

        Returns:
            str: 简短摘要
        """
        if not bundle.evidences:
            return "无证据"

        if bundle.mode == "G1":
            return f"推荐得分: {bundle.normalized_score:.0%}"
        elif bundle.mode == "G2":
            conflicts = len([
                e for e in bundle.evidences
                if e.evidence_type == EvidenceType.CONFLICT_INDICATOR
            ])
            return f"检测到 {conflicts} 处冲突"
        elif bundle.mode == "G5":
            if bundle.normalized_score >= 0.75:
                return "风险等级: 高"
            elif bundle.normalized_score >= 0.5:
                return "风险等级: 中"
            else:
                return "风险等级: 低"
        else:
            return f"得分: {bundle.normalized_score:.0%}"

    def build_factors_list(
        self,
        bundle: EvidenceBundle,
        max_factors: int = 5,
    ) -> list[dict[str, Any]]:
        """
        构建因素列表

        用于结构化输出（如 JSON 格式）。

        Args:
            bundle: 证据包
            max_factors: 最大因素数

        Returns:
            list[dict]: 因素列表
        """
        factors = []
        top_contributors = bundle.get_top_k_contributors(max_factors)

        for contrib in top_contributors:
            evidence = bundle.get_evidence_by_id(contrib.evidence_id)
            if evidence:
                factors.append({
                    "type": evidence.evidence_type.value,
                    "type_label": self.TYPE_LABELS.get(
                        evidence.evidence_type,
                        evidence.evidence_type.value
                    ),
                    "contribution": round(contrib.contribution_ratio, 4),
                    "raw_value": round(evidence.raw_value, 4),
                    "weight": round(evidence.weight, 4),
                    "source": evidence.source.value,
                    "source_label": self.SOURCE_LABELS.get(
                        evidence.source,
                        evidence.source.value
                    ),
                    "description": evidence.description,
                    "supporting_facts": evidence.supporting_facts[:3],
                })

        return factors

    def build_source_attribution(
        self,
        bundle: EvidenceBundle,
    ) -> dict[str, Any]:
        """
        构建来源归因

        显示各来源的贡献占比。

        Args:
            bundle: 证据包

        Returns:
            dict: 来源归因信息
        """
        if not bundle.evidences:
            return {"total": 0, "by_source": {}}

        # 按来源统计加权值
        source_values: dict[str, float] = {}
        for evidence in bundle.evidences:
            source = evidence.source.value
            source_values[source] = source_values.get(source, 0) + evidence.weighted_value

        total = sum(source_values.values()) if source_values else 0

        by_source = {}
        for source, value in source_values.items():
            by_source[source] = {
                "value": round(value, 4),
                "ratio": round(value / total, 4) if total > 0 else 0,
                "label": self.SOURCE_LABELS.get(
                    EvidenceSource(source),
                    source
                ),
            }

        return {
            "total": round(total, 4),
            "by_source": by_source,
        }


# === 全局单例 ===

_explanation_builder: Optional[ExplanationBuilderV2] = None


def get_explanation_builder() -> ExplanationBuilderV2:
    """获取全局构建器实例"""
    global _explanation_builder
    if _explanation_builder is None:
        _explanation_builder = ExplanationBuilderV2()
    return _explanation_builder


def reset_explanation_builder() -> None:
    """重置构建器实例（用于测试）"""
    global _explanation_builder
    _explanation_builder = None


__all__ = [
    "ExplanationStyle",
    "ExplanationBuilderV2",
    "get_explanation_builder",
    "reset_explanation_builder",
]