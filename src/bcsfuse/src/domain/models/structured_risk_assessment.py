"""
Structured Risk Assessment

G5 Risk Engine V2 结构化风险评估模型。

定义 G5 专家诊断场景的结构化风险评估输出字段。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.domain.models.expert_risk_assessment import RiskLevel


class RiskFactor(BaseModel):
    """
    风险因素

    描述一个具体的风险因素。

    Attributes:
        factor_id: 因素标识符
        description: 风险描述
        category: 风险类别（安全/合规/性能/财务等）
        severity: 严重程度
        likelihood: 发生可能性
        impact: 影响程度
        evidence: 支持证据列表
        expert_sources: 专家来源列表
    """

    factor_id: str = Field(description="因素标识符")
    description: str = Field(description="风险描述")
    category: str = Field(
        default="general",
        description="风险类别（安全/合规/性能/财务等）",
    )
    severity: RiskLevel = Field(description="严重程度")
    likelihood: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="发生可能性",
    )
    impact: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="影响程度",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="支持证据列表",
    )
    expert_sources: list[str] = Field(
        default_factory=list,
        description="专家来源列表",
    )


class BlockingCondition(BaseModel):
    """
    阻塞条件

    描述一个阻止上线的条件。

    Attributes:
        condition_id: 条件标识符
        description: 条件描述
        blocking_reason: 阻塞原因
        required_actions: 需要采取的行动
    """

    condition_id: str = Field(description="条件标识符")
    description: str = Field(description="条件描述")
    blocking_reason: str = Field(description="阻塞原因")
    required_actions: list[str] = Field(
        default_factory=list,
        description="需要采取的行动",
    )


class ExpertEvidence(BaseModel):
    """
    专家证据

    描述来自专家的证据信息。

    Attributes:
        expert_id: 专家标识符
        expert_domain: 专家领域
        evidence_text: 证据文本
        evidence_type: 证据类型（事实/观点/顾虑/建议）
        confidence: 置信度
    """

    expert_id: str = Field(description="专家标识符")
    expert_domain: str = Field(description="专家领域")
    evidence_text: str = Field(description="证据文本")
    evidence_type: Literal["fact", "opinion", "concern", "recommendation"] = Field(
        default="opinion",
        description="证据类型",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="置信度",
    )


class ScenarioPriorRisk(BaseModel):
    """
    场景先验风险

    基于问题场景推断的先验风险。

    Attributes:
        scenario_type: 场景类型
        matched_keywords: 匹配的关键词
        baseline_risk: 基线风险等级
        confidence: 置信度
    """

    scenario_type: str = Field(description="场景类型")
    matched_keywords: list[str] = Field(
        default_factory=list,
        description="匹配的关键词",
    )
    baseline_risk: RiskLevel = Field(description="基线风险等级")
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="置信度",
    )


class StructuredRiskAssessment(BaseModel):
    """
    结构化风险评估 (G5 V2)

    G5 专家诊断的结构化风险评估输出。

    包含:
    - 整体风险等级
    - 基线风险（基于场景）
    - 风险因素列表
    - 阻塞条件列表
    - 支持证据列表
    - 决策理由
    - 场景先验风险

    Attributes:
        risk_level: 最终风险等级
        baseline_risk: 场景推断的基线风险
        risk_factors: 风险因素列表
        blocking_conditions: 阻塞条件列表
        supporting_evidence: 支持证据列表
        decision_rationale: 决策理由
        scenario_prior_risk: 场景先验风险
    """

    risk_level: RiskLevel = Field(description="最终风险等级")
    baseline_risk: Optional[RiskLevel] = Field(
        default=None,
        description="场景推断的基线风险",
    )
    risk_factors: list[RiskFactor] = Field(
        default_factory=list,
        description="风险因素列表",
    )
    blocking_conditions: list[BlockingCondition] = Field(
        default_factory=list,
        description="阻塞条件列表",
    )
    supporting_evidence: list[ExpertEvidence] = Field(
        default_factory=list,
        description="支持证据列表",
    )
    decision_rationale: str = Field(
        default="",
        description="决策理由",
    )
    scenario_prior_risk: Optional[ScenarioPriorRisk] = Field(
        default=None,
        description="场景先验风险",
    )

    def has_blocking_conditions(self) -> bool:
        """
        检查是否有阻塞条件

        Returns:
            bool: 是否存在阻塞条件
        """
        return len(self.blocking_conditions) > 0

    def get_high_severity_factors(self) -> list[RiskFactor]:
        """
        获取高严重程度的风险因素

        Returns:
            list[RiskFactor]: 高严重程度风险因素列表
        """
        return [
            f
            for f in self.risk_factors
            if f.severity in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        ]


__all__ = [
    "RiskFactor",
    "BlockingCondition",
    "ExpertEvidence",
    "ScenarioPriorRisk",
    "StructuredRiskAssessment",
]