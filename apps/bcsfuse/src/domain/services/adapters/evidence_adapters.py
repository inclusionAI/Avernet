"""
Evidence Adapters - Legacy Signal to Evidence Conversion

Phase D: Unified Evidence Layer

提供 G1/G2/G5 各模式 Legacy Signal 模型到统一 Evidence 模型的转换。

设计原则：
- 单向转换：Legacy -> Evidence
- 无损转换：保留所有原始信息
- 来源标注：清晰标记来源组件

约束：
- 内部适配器，不暴露到API
- Feature Flag 控制是否启用
- 默认禁用，不影响现有行为
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal, Optional

from src.domain.models.evidence import (
    Evidence,
    EvidenceSource,
    EvidenceType,
)
from src.domain.models.scoring_signal import ScoringSignal, SignalType
from src.domain.models.stance_signal import StanceSignal
from src.domain.models.structured_risk_assessment import (
    RiskFactor,
    ExpertEvidence,
    ScenarioPriorRisk,
)

logger = logging.getLogger(__name__)


# =============================================================================
# G1 Adapter: ScoringSignal -> Evidence
# =============================================================================

# Signal Type 到 Evidence Type 的映射
G1_SIGNAL_TYPE_MAP: dict[str, EvidenceType] = {
    SignalType.CONTEXT_MATCH: EvidenceType.SEMANTIC_SIMILARITY,
    SignalType.SKILL_NAME_MATCH: EvidenceType.SKILL_MATCH,
    SignalType.SKILL_DESC_MATCH: EvidenceType.SKILL_MATCH,
    SignalType.SEARCHABLE_MATCH: EvidenceType.SEMANTIC_SIMILARITY,
    SignalType.COVERAGE_SCORE: EvidenceType.CAPABILITY_COVERAGE,
    SignalType.DOMAIN_COVERAGE: EvidenceType.DOMAIN_EXPERTISE,
    SignalType.PROFILE_TYPE_BONUS: EvidenceType.AVAILABILITY,
    SignalType.MODE_BONUS: EvidenceType.SCENARIO_MATCH,
}


def scoring_signal_to_evidence(
    signal: ScoringSignal,
    participant_id: Optional[str] = None,
    source: EvidenceSource = EvidenceSource.RULE_BASED,
    description: Optional[str] = None,
) -> Evidence:
    """
    将 G1 ScoringSignal 转换为统一 Evidence

    Args:
        signal: ScoringSignal 实例
        participant_id: 关联的参与者ID
        source: 证据来源（默认 RULE_BASED）
        description: 自定义描述

    Returns:
        Evidence: 统一证据模型
    """
    # 确定 EvidenceType
    evidence_type = G1_SIGNAL_TYPE_MAP.get(
        signal.signal_type,
        EvidenceType.SEMANTIC_SIMILARITY,  # 默认类型
    )

    # 生成证据ID
    evidence_id = f"ev_g1_{signal.signal_type}_{uuid.uuid4().hex[:8]}"

    # 构建描述
    if description is None:
        description = f"G1 scoring signal: {signal.signal_type}"

    # 构建 supporting_facts
    supporting_facts = []
    if signal.details:
        for key, value in signal.details.items():
            if isinstance(value, (str, int, float, bool)):
                supporting_facts.append(f"{key}: {value}")

    # 构建 provenance
    provenance: dict[str, Any] = {
        "adapter": "g1_evidence_adapter",
        "original_type": "ScoringSignal",
        "original_signal_type": signal.signal_type,
        "original_details": signal.details,
    }

    logger.debug(
        f"[EvidenceAdapter] G1 ScoringSignal -> Evidence: "
        f"signal_type={signal.signal_type} -> evidence_type={evidence_type.value}, "
        f"raw_score={signal.raw_score:.4f}, weight={signal.weight:.4f}"
    )

    return Evidence(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        source=source,
        mode="G1",
        raw_value=signal.raw_score,
        weight=signal.weight,
        weighted_value=signal.weighted_score or (signal.raw_score * signal.weight),
        description=description,
        supporting_facts=supporting_facts,
        provenance=provenance,
        confidence=1.0,  # G1 signal 不记录置信度，默认 1.0
        participant_id=participant_id,
    )


def scoring_signals_to_evidences(
    signals: list[ScoringSignal],
    participant_id: Optional[str] = None,
    source: EvidenceSource = EvidenceSource.RULE_BASED,
) -> list[Evidence]:
    """
    批量转换 G1 ScoringSignal 列表

    Args:
        signals: ScoringSignal 列表
        participant_id: 关联的参与者ID
        source: 证据来源

    Returns:
        list[Evidence]: Evidence 列表
    """
    return [
        scoring_signal_to_evidence(s, participant_id, source)
        for s in signals
    ]


# =============================================================================
# G2 Adapter: StanceSignal -> Evidence
# =============================================================================

def stance_signal_to_evidence(
    stance: StanceSignal,
    source: EvidenceSource = EvidenceSource.LLM_INFERENCE,
    description: Optional[str] = None,
) -> Evidence:
    """
    将 G2 StanceSignal 转换为统一 Evidence

    Args:
        stance: StanceSignal 实例
        source: 证据来源（默认 LLM_INFERENCE）
        description: 自定义描述

    Returns:
        Evidence: 统一证据模型
    """
    # 确定 EvidenceType
    # - axis_a/axis_b 强立场 -> STANCE
    # - balanced 平衡立场 -> ALIGNMENT_INDICATOR
    # - neutral/unknown -> 降级为低权重 STANCE
    if stance.position in ("axis_a", "axis_b"):
        evidence_type = EvidenceType.STANCE
    elif stance.position == "balanced":
        evidence_type = EvidenceType.ALIGNMENT_INDICATOR
    else:
        evidence_type = EvidenceType.STANCE

    # 生成证据ID
    evidence_id = f"ev_g2_stance_{stance.dimension_id}_{uuid.uuid4().hex[:8]}"

    # 构建描述
    if description is None:
        description = (
            f"G2 stance on {stance.dimension_id}: "
            f"position={stance.position}, strength={stance.strength:.2f}"
        )

    # 计算 raw_value
    # - strength 作为基础值
    # - confidence 影响权重
    raw_value = stance.strength

    # 构建 supporting_facts
    supporting_facts = list(stance.evidence)
    if stance.rationale:
        supporting_facts.append(f"Rationale: {stance.rationale}")

    # 构建 provenance
    provenance: dict[str, Any] = {
        "adapter": "g2_evidence_adapter",
        "original_type": "StanceSignal",
        "dimension_id": stance.dimension_id,
        "position": stance.position,
        "is_meaningful": stance.is_meaningful(),
    }

    logger.debug(
        f"[EvidenceAdapter] G2 StanceSignal -> Evidence: "
        f"dimension={stance.dimension_id}, position={stance.position}, "
        f"strength={stance.strength:.4f}, confidence={stance.confidence:.4f}, "
        f"evidence_type={evidence_type.value}"
    )

    return Evidence(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        source=source,
        mode="G2",
        raw_value=raw_value,
        weight=stance.confidence,  # 使用置信度作为权重
        weighted_value=raw_value * stance.confidence,
        description=description,
        supporting_facts=supporting_facts,
        provenance=provenance,
        confidence=stance.confidence,
        participant_id=stance.participant_id,
    )


def stance_signals_to_evidences(
    stances: list[StanceSignal],
    source: EvidenceSource = EvidenceSource.LLM_INFERENCE,
) -> list[Evidence]:
    """
    批量转换 G2 StanceSignal 列表

    Args:
        stances: StanceSignal 列表
        source: 证据来源

    Returns:
        list[Evidence]: Evidence 列表
    """
    return [stance_signal_to_evidence(s, source) for s in stances]


def create_conflict_evidence(
    conflict_description: str,
    parties: list[str],
    severity: Literal["low", "medium", "high", "critical"],
    source: EvidenceSource = EvidenceSource.RULE_BASED,
    details: Optional[dict[str, Any]] = None,
) -> Evidence:
    """
    创建 G2 冲突证据

    用于表示检测到的冲突。

    Args:
        conflict_description: 冲突描述
        parties: 冲突涉及的参与者列表
        severity: 冲突严重程度
        source: 证据来源
        details: 额外详情

    Returns:
        Evidence: 冲突证据
    """
    evidence_id = f"ev_g2_conflict_{uuid.uuid4().hex[:8]}"

    # severity -> raw_value 映射
    severity_map = {
        "low": 0.25,
        "medium": 0.5,
        "high": 0.75,
        "critical": 1.0,
    }
    raw_value = severity_map.get(severity, 0.5)

    weight_map = {
        "low": 0.3,
        "medium": 0.5,
        "high": 0.8,
        "critical": 1.0,
    }
    weight = weight_map.get(severity, 0.5)

    supporting_facts = [f"Parties: {', '.join(parties)}"]
    if details:
        for k, v in details.items():
            supporting_facts.append(f"{k}: {v}")

    return Evidence(
        evidence_id=evidence_id,
        evidence_type=EvidenceType.CONFLICT_INDICATOR,
        source=source,
        mode="G2",
        raw_value=raw_value,
        weight=weight,
        weighted_value=raw_value * weight,
        description=conflict_description,
        supporting_facts=supporting_facts,
        provenance={
            "adapter": "g2_evidence_adapter",
            "conflict_parties": parties,
            "severity": severity,
            "details": details or {},
        },
        confidence=1.0,
    )


# =============================================================================
# G5 Adapter: RiskFactor/ExpertEvidence -> Evidence
# =============================================================================

# RiskLevel 到数值的映射
RISK_LEVEL_MAP = {
    "low": 0.25,
    "medium": 0.5,
    "high": 0.75,
    "critical": 1.0,
}


def risk_factor_to_evidence(
    factor: RiskFactor,
    source: EvidenceSource = EvidenceSource.RULE_BASED,
    description: Optional[str] = None,
) -> Evidence:
    """
    将 G5 RiskFactor 转换为统一 Evidence

    Args:
        factor: RiskFactor 实例
        source: 证据来源（默认 RULE_BASED）
        description: 自定义描述

    Returns:
        Evidence: 统一证据模型
    """
    evidence_id = f"ev_g5_risk_{factor.factor_id}_{uuid.uuid4().hex[:8]}"

    if description is None:
        description = f"G5 risk factor: {factor.description}"

    # 计算 raw_value
    # 基于 severity, likelihood, impact 综合计算
    severity_value = RISK_LEVEL_MAP.get(factor.severity.value, 0.5)
    likelihood_map = {"high": 0.8, "medium": 0.5, "low": 0.2}
    impact_map = {"high": 0.8, "medium": 0.5, "low": 0.2}

    likelihood_value = likelihood_map.get(factor.likelihood, 0.5)
    impact_value = impact_map.get(factor.impact, 0.5)

    # raw_value = severity * 0.5 + likelihood * 0.25 + impact * 0.25
    raw_value = severity_value * 0.5 + likelihood_value * 0.25 + impact_value * 0.25

    # supporting_facts
    supporting_facts = list(factor.evidence)
    supporting_facts.append(f"Category: {factor.category}")
    supporting_facts.append(f"Severity: {factor.severity.value}")
    supporting_facts.append(f"Likelihood: {factor.likelihood}")
    supporting_facts.append(f"Impact: {factor.impact}")

    # provenance
    provenance: dict[str, Any] = {
        "adapter": "g5_evidence_adapter",
        "original_type": "RiskFactor",
        "factor_id": factor.factor_id,
        "category": factor.category,
        "severity": factor.severity.value,
        "likelihood": factor.likelihood,
        "impact": factor.impact,
        "expert_sources": factor.expert_sources,
    }

    logger.debug(
        f"[EvidenceAdapter] G5 RiskFactor -> Evidence: "
        f"factor_id={factor.factor_id}, category={factor.category}, "
        f"severity={factor.severity.value}, raw_value={raw_value:.4f}"
    )

    return Evidence(
        evidence_id=evidence_id,
        evidence_type=EvidenceType.RISK_FACTOR,
        source=source,
        mode="G5",
        raw_value=raw_value,
        weight=0.8,  # RiskFactor 权重较高
        weighted_value=raw_value * 0.8,
        description=description,
        supporting_facts=supporting_facts,
        provenance=provenance,
        confidence=severity_value,  # 使用 severity 作为置信度参考
    )


def risk_factors_to_evidences(
    factors: list[RiskFactor],
    source: EvidenceSource = EvidenceSource.RULE_BASED,
) -> list[Evidence]:
    """
    批量转换 G5 RiskFactor 列表

    Args:
        factors: RiskFactor 列表
        source: 证据来源

    Returns:
        list[Evidence]: Evidence 列表
    """
    return [risk_factor_to_evidence(f, source) for f in factors]


def expert_evidence_to_evidence(
    expert_evidence: ExpertEvidence,
    source: EvidenceSource = EvidenceSource.LLM_INFERENCE,
) -> Evidence:
    """
    将 G5 ExpertEvidence 转换为统一 Evidence

    Args:
        expert_evidence: ExpertEvidence 实例
        source: 证据来源

    Returns:
        Evidence: 统一证据模型
    """
    evidence_id = f"ev_g5_expert_{expert_evidence.expert_id}_{uuid.uuid4().hex[:8]}"

    # evidence_type 映射
    type_map = {
        "fact": EvidenceType.RISK_FACTOR,
        "opinion": EvidenceType.RISK_FACTOR,
        "concern": EvidenceType.RISK_FACTOR,
        "recommendation": EvidenceType.SCENARIO_MATCH,
    }
    evidence_type = type_map.get(expert_evidence.evidence_type, EvidenceType.RISK_FACTOR)

    description = f"G5 expert evidence from {expert_evidence.expert_domain}: {expert_evidence.evidence_text[:100]}..."

    supporting_facts = [
        f"Expert: {expert_evidence.expert_id}",
        f"Domain: {expert_evidence.expert_domain}",
        f"Type: {expert_evidence.evidence_type}",
    ]

    return Evidence(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        source=source,
        mode="G5",
        raw_value=expert_evidence.confidence,
        weight=0.7,  # Expert evidence 权重适中
        weighted_value=expert_evidence.confidence * 0.7,
        description=description,
        supporting_facts=supporting_facts,
        provenance={
            "adapter": "g5_evidence_adapter",
            "original_type": "ExpertEvidence",
            "expert_id": expert_evidence.expert_id,
            "expert_domain": expert_evidence.expert_domain,
            "evidence_type": expert_evidence.evidence_type,
        },
        confidence=expert_evidence.confidence,
        participant_id=expert_evidence.expert_id,
    )


def scenario_prior_to_evidence(
    scenario: ScenarioPriorRisk,
    source: EvidenceSource = EvidenceSource.TAXONOMY_PRIOR,
) -> Evidence:
    """
    将 G5 ScenarioPriorRisk 转换为统一 Evidence

    Args:
        scenario: ScenarioPriorRisk 实例
        source: 证据来源

    Returns:
        Evidence: 统一证据模型
    """
    evidence_id = f"ev_g5_scenario_{uuid.uuid4().hex[:8]}"

    raw_value = RISK_LEVEL_MAP.get(scenario.baseline_risk.value, 0.5)

    supporting_facts = [f"Scenario: {scenario.scenario_type}"]
    supporting_facts.extend([f"Keyword: {kw}" for kw in scenario.matched_keywords])

    return Evidence(
        evidence_id=evidence_id,
        evidence_type=EvidenceType.SCENARIO_MATCH,
        source=source,
        mode="G5",
        raw_value=raw_value,
        weight=scenario.confidence * 0.6,  # Prior 权重降低
        weighted_value=raw_value * scenario.confidence * 0.6,
        description=f"G5 scenario prior risk: {scenario.scenario_type}",
        supporting_facts=supporting_facts,
        provenance={
            "adapter": "g5_evidence_adapter",
            "original_type": "ScenarioPriorRisk",
            "scenario_type": scenario.scenario_type,
            "matched_keywords": scenario.matched_keywords,
        },
        confidence=scenario.confidence,
    )


# =============================================================================
# Adapter Registry
# =============================================================================

class EvidenceAdapterRegistry:
    """
    证据适配器注册表

    统一管理所有适配器，便于扩展和测试。
    """

    def __init__(self):
        """初始化注册表"""
        self._adapters: dict[str, Any] = {}

    def register(self, name: str, adapter: Any) -> None:
        """注册适配器"""
        self._adapters[name] = adapter

    def get(self, name: str) -> Optional[Any]:
        """获取适配器"""
        return self._adapters.get(name)

    def list_adapters(self) -> list[str]:
        """列出所有适配器名称"""
        return list(self._adapters.keys())


# 全局注册表实例
_adapter_registry = EvidenceAdapterRegistry()

# 注册默认适配器
_adapter_registry.register("g1_scoring_signal", scoring_signal_to_evidence)
_adapter_registry.register("g2_stance_signal", stance_signal_to_evidence)
_adapter_registry.register("g2_conflict", create_conflict_evidence)
_adapter_registry.register("g5_risk_factor", risk_factor_to_evidence)
_adapter_registry.register("g5_expert_evidence", expert_evidence_to_evidence)
_adapter_registry.register("g5_scenario_prior", scenario_prior_to_evidence)


def get_adapter_registry() -> EvidenceAdapterRegistry:
    """获取全局适配器注册表"""
    return _adapter_registry


__all__ = [
    # G1 Adapters
    "scoring_signal_to_evidence",
    "scoring_signals_to_evidences",
    "G1_SIGNAL_TYPE_MAP",
    # G2 Adapters
    "stance_signal_to_evidence",
    "stance_signals_to_evidences",
    "create_conflict_evidence",
    # G5 Adapters
    "risk_factor_to_evidence",
    "risk_factors_to_evidences",
    "expert_evidence_to_evidence",
    "scenario_prior_to_evidence",
    "RISK_LEVEL_MAP",
    # Registry
    "EvidenceAdapterRegistry",
    "get_adapter_registry",
]