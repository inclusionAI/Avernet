"""
Taxonomy Models

领域分类模型定义。

定义领域、场景、风险信号等分类体系的数据结构。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DomainDefinition(BaseModel):
    """
    领域定义

    描述一个业务或技术领域。

    Attributes:
        name: 领域名称
        description: 领域描述
        keywords: 关联关键词列表
        related_expert_types: 相关专家类型
    """

    name: str = Field(description="领域名称")
    description: str = Field(description="领域描述")
    keywords: list[str] = Field(
        default_factory=list,
        description="关联关键词列表",
    )
    related_expert_types: list[str] = Field(
        default_factory=list,
        description="相关专家类型",
    )


class ScenarioDefinition(BaseModel):
    """
    场景定义

    描述一个业务场景。

    Attributes:
        name: 场景名称
        description: 场景描述
        category: 场景分类
        risk_weight: 风险权重
        keywords: 关联关键词列表
    """

    name: str = Field(description="场景名称")
    description: str = Field(description="场景描述")
    category: str = Field(default="general", description="场景分类")
    risk_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="风险权重",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="关联关键词列表",
    )


class RiskSignalDefinition(BaseModel):
    """
    风险信号定义

    描述一个风险信号场景。

    Attributes:
        name: 信号名称
        description: 信号描述
        keywords: 触发关键词列表
        weight: 风险权重
    """

    name: str = Field(description="信号名称")
    description: str = Field(description="信号描述")
    keywords: list[str] = Field(
        default_factory=list,
        description="触发关键词列表",
    )
    weight: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="风险权重",
    )


class RiskLevelKeywords(BaseModel):
    """
    风险等级关键词配置

    按风险等级分类的关键词配置。

    Attributes:
        critical: 严重风险关键词
        high: 高风险关键词
        medium: 中风险关键词
    """

    critical: list[str] = Field(
        default_factory=list,
        description="严重风险关键词",
    )
    high: list[str] = Field(
        default_factory=list,
        description="高风险关键词",
    )
    medium: list[str] = Field(
        default_factory=list,
        description="中风险关键词",
    )


class DomainsConfig(BaseModel):
    """
    领域配置

    包含技术领域和业务领域的配置。

    Attributes:
        technical_domains: 技术领域配置
        business_domains: 业务领域配置
    """

    technical_domains: dict[str, DomainDefinition] = Field(
        default_factory=dict,
        description="技术领域配置",
    )
    business_domains: dict[str, DomainDefinition] = Field(
        default_factory=dict,
        description="业务领域配置",
    )


class ScenariosConfig(BaseModel):
    """
    场景配置

    包含业务场景和风险权重的配置。

    Attributes:
        business_scenarios: 业务场景配置
        risk_weights: 风险权重配置
        scenario_priorities: 场景优先级配置
    """

    business_scenarios: dict[str, ScenarioDefinition] = Field(
        default_factory=dict,
        description="业务场景配置",
    )
    risk_weights: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="风险权重配置",
    )
    scenario_priorities: dict[str, list[str]] = Field(
        default_factory=dict,
        description="场景优先级配置",
    )


class RiskSignalsConfig(BaseModel):
    """
    风险信号配置

    包含各风险等级场景的定义。

    Attributes:
        critical_scenarios: 严重风险场景
        high_scenarios: 高风险场景
        medium_scenarios: 中风险场景
        risk_level_keywords: 风险等级关键词
    """

    critical_scenarios: dict[str, RiskSignalDefinition] = Field(
        default_factory=dict,
        description="严重风险场景",
    )
    high_scenarios: dict[str, RiskSignalDefinition] = Field(
        default_factory=dict,
        description="高风险场景",
    )
    medium_scenarios: dict[str, RiskSignalDefinition] = Field(
        default_factory=dict,
        description="中风险场景",
    )
    risk_level_keywords: RiskLevelKeywords = Field(
        default_factory=RiskLevelKeywords,
        description="风险等级关键词",
    )


# =============================================================================
# G2 Conflict Dimensions (Phase B)
# =============================================================================

class ConflictDimensionAxis(BaseModel):
    """
    冲突维度轴向定义

    描述一个冲突维度的一端。

    Attributes:
        label: 轴向标签（如 speed, quality）
        description: 轴向描述
        keywords: 触发该轴向的关键词列表
    """

    label: str = Field(description="轴向标签")
    description: str = Field(description="轴向描述")
    keywords: list[str] = Field(
        default_factory=list,
        description="触发该轴向的关键词列表",
    )


class ConflictDimensionDefinition(BaseModel):
    """
    冲突维度定义

    描述一个完整的冲突维度，包含两端轴向。

    Attributes:
        name: 维度名称
        description: 维度描述
        axis_a: 一端轴向
        axis_b: 另一端轴向
    """

    name: str = Field(description="维度名称")
    description: str = Field(description="维度描述")
    axis_a: ConflictDimensionAxis = Field(description="一端轴向")
    axis_b: ConflictDimensionAxis = Field(description="另一端轴向")


class ConflictDimensionsConfig(BaseModel):
    """
    冲突维度配置

    包含所有冲突维度和判定阈值。

    Attributes:
        dimensions: 冲突维度字典
        thresholds: 判定阈值配置
    """

    dimensions: dict[str, ConflictDimensionDefinition] = Field(
        default_factory=dict,
        description="冲突维度配置",
    )
    thresholds: dict[str, float] = Field(
        default_factory=lambda: {
            "conflict_strength_threshold": 0.6,
            "alignment_strength_threshold": 0.3,
            "tension_strength_min": 0.3,
            "tension_strength_max": 0.6,
            "min_confidence_threshold": 0.4,
        },
        description="判定阈值配置",
    )


class TaxonomyConfig(BaseModel):
    """
    分类体系配置（完整）

    包含所有分类配置的集合。

    Attributes:
        domains: 领域配置
        scenarios: 场景配置
        risk_signals: 风险信号配置
        conflict_dimensions: 冲突维度配置（G2 Phase B）
    """

    domains: DomainsConfig = Field(
        default_factory=DomainsConfig,
        description="领域配置",
    )
    scenarios: ScenariosConfig = Field(
        default_factory=ScenariosConfig,
        description="场景配置",
    )
    risk_signals: RiskSignalsConfig = Field(
        default_factory=RiskSignalsConfig,
        description="风险信号配置",
    )
    conflict_dimensions: ConflictDimensionsConfig = Field(
        default_factory=ConflictDimensionsConfig,
        description="冲突维度配置（G2 Phase B）",
    )


__all__ = [
    "DomainDefinition",
    "ScenarioDefinition",
    "RiskSignalDefinition",
    "RiskLevelKeywords",
    "DomainsConfig",
    "ScenariosConfig",
    "RiskSignalsConfig",
    "ConflictDimensionAxis",
    "ConflictDimensionDefinition",
    "ConflictDimensionsConfig",
    "TaxonomyConfig",
]