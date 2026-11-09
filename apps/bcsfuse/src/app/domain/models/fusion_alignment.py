"""
FusionAlignmentPoint

G2: Conflict Alignment Layer

对齐点领域模型定义，用于描述 G2 场景中多方达成的共识或对齐点。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FusionAlignmentPoint(BaseModel):
    """
    融合对齐点

    描述 G2 场景中多方达成的共识或对齐点。

    Attributes:
        summary: 共识/对齐点摘要描述
        participants: 参与对齐的各方（可选）
    """

    model_config = {"extra": "forbid"}

    summary: str = Field(
        min_length=1,
        max_length=1000,
        description="共识/对齐点摘要描述",
    )

    participants: Optional[list[str]] = Field(
        default=None,
        max_length=10,
        description="参与对齐的各方（可选，None 表示全部参与者）",
    )


__all__ = [
    "FusionAlignmentPoint",
]