"""
Expert Risk Assessment

G5: Expert Diagnosis Layer

风险评估领域模型定义，用于描述 G5 专家会诊场景的整体风险与分领域风险。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    """
    风险等级枚举

    描述整体风险或分领域风险的等级。

    Values:
        LOW: 低风险
        MEDIUM: 中风险
        HIGH: 高风险
        CRITICAL: 严重风险
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Domain(str, Enum):
    """
    领域枚举

    描述专家会诊涉及的领域，统一命名规范。

    Values:
        SECURITY: 安全领域
        LEGAL: 法务领域
        DATABASE: 数据库领域
        OPS: 运维领域
        TECH: 技术/开发领域
        ARCHITECTURE: 架构领域
    """

    SECURITY = "security"
    LEGAL = "legal"
    DATABASE = "database"
    OPS = "ops"
    TECH = "tech"
    ARCHITECTURE = "architecture"


class RiskAssessment(BaseModel):
    """
    风险评估模型

    G5 专家会诊场景的风险评估结果。

    Attributes:
        overall: 整体风险等级
        categories: 分领域风险等级映射（key: 领域名，value: 风险等级）
    """

    model_config = {"extra": "forbid"}

    overall: RiskLevel = Field(
        description="整体风险等级",
    )

    categories: dict[str, RiskLevel] = Field(
        default_factory=dict,
        description="分领域风险等级映射",
    )


__all__ = [
    "RiskLevel",
    "Domain",
    "RiskAssessment",
]