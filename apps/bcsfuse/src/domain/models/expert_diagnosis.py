"""
Expert Diagnosis Models

G5: Expert Diagnosis Layer

专家会诊关键问题和建议模型定义。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from src.domain.models.expert_risk_assessment import RiskLevel


class Priority(str, Enum):
    """
    优先级枚举

    描述专家建议的优先级。

    Values:
        P0: 最高优先级（必须立即处理）
        P1: 高优先级（本迭代内处理）
        P2: 中优先级（后续迭代处理）
    """

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class CriticalIssue(BaseModel):
    """
    关键问题模型

    描述 G5 专家会诊中发现的关键问题。

    Attributes:
        issue: 问题描述
        severity: 严重程度（使用 RiskLevel）
        domain: 问题领域
        source: 问题来源方（如 anquan, fawu, dba）
        description: 详细描述（可选）
    """

    model_config = {"extra": "forbid"}

    issue: str = Field(
        min_length=1,
        max_length=1000,
        description="问题描述",
    )

    severity: RiskLevel = Field(
        description="严重程度",
    )

    domain: str = Field(
        min_length=1,
        max_length=100,
        description="问题领域",
    )

    source: str = Field(
        min_length=1,
        max_length=100,
        description="问题来源方",
    )

    description: str | None = Field(
        default=None,
        max_length=2000,
        description="详细描述",
    )


class ExpertRecommendation(BaseModel):
    """
    专家建议模型

    描述 G5 专家会诊产生的行动建议。

    Attributes:
        priority: 优先级（P0/P1/P2）
        action: 建议行动
        owner: 责任方（可选）
        domain: 领域（可选）
        deadline: 截止时间（可选）
    """

    model_config = {"extra": "forbid"}

    priority: Priority = Field(
        description="优先级",
    )

    action: str = Field(
        min_length=1,
        max_length=1000,
        description="建议行动",
    )

    owner: str | None = Field(
        default=None,
        max_length=100,
        description="责任方",
    )

    domain: str | None = Field(
        default=None,
        max_length=100,
        description="领域",
    )

    deadline: datetime | None = Field(
        default=None,
        description="截止时间",
    )


# Type alias for go_live_conditions
GoLiveCondition = list[str]
"""上线条件列表，每项为字符串"""


__all__ = [
    "Priority",
    "CriticalIssue",
    "ExpertRecommendation",
    "GoLiveCondition",
]