"""
Taxonomy Module

分类体系模块。

提供领域、场景、风险信号等分类体系的配置和查询功能。
"""

from src.domain.taxonomy.models import (
    DomainDefinition,
    DomainsConfig,
    RiskLevelKeywords,
    RiskSignalDefinition,
    RiskSignalsConfig,
    ScenarioDefinition,
    ScenariosConfig,
    TaxonomyConfig,
)
from src.domain.taxonomy.registry import (
    TaxonomyRegistry,
    get_taxonomy_registry,
    reset_taxonomy_registry,
)

__all__ = [
    # Models
    "DomainDefinition",
    "ScenarioDefinition",
    "RiskSignalDefinition",
    "RiskLevelKeywords",
    "DomainsConfig",
    "ScenariosConfig",
    "RiskSignalsConfig",
    "TaxonomyConfig",
    # Registry
    "TaxonomyRegistry",
    "get_taxonomy_registry",
    "reset_taxonomy_registry",
]