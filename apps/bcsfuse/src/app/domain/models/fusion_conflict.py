"""
FusionConflict

G2: Conflict Alignment Layer

冲突领域模型定义，用于描述 G2 场景中多方视角的冲突。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """
    冲突严重程度枚举

    描述冲突的严重程度。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FusionConflict(BaseModel):
    """
    融合冲突

    描述 G2 场景中多方视角之间的冲突。

    Attributes:
        parties: 冲突相关方列表（至少 2 个）
        issue: 冲突议题描述
        positions: 各方立场列表
        severity: 冲突严重程度
    """

    model_config = {"extra": "forbid"}

    parties: list[str] = Field(
        min_length=2,
        max_length=10,
        description="冲突相关方列表",
    )

    issue: str = Field(
        min_length=1,
        max_length=500,
        description="冲突议题描述",
    )

    positions: list[str] = Field(
        min_length=2,
        max_length=10,
        description="各方立场列表",
    )

    severity: Literal["low", "medium", "high", "critical"] = Field(
        description="冲突严重程度",
    )


__all__ = [
    "FusionConflict",
    "Severity",
]