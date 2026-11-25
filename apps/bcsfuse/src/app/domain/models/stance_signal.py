"""
StanceSignal - 立场信号模型

G2 Conflict Alignment Layer - Phase B

描述参与者在某一冲突维度上的立场信号。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class StanceSignal(BaseModel):
    """
    立场信号

    描述单个参与者在某一冲突维度上的立场。

    Attributes:
        participant_id: 参与者 ID
        dimension_id: 冲突维度 ID（如 speed_vs_quality）
        position: 立场位置
            - axis_a: 倾向维度 A 端（如 speed）
            - axis_b: 倾向维度 B 端（如 quality）
            - balanced: 两端都有倾向，处于平衡
            - neutral: 在该维度上无明显倾向
            - unknown: 无法判断
        strength: 立场强度 (0.0-1.0)
        confidence: 置信度 (0.0-1.0)
        evidence: 支持该立场的证据（关键词或文本片段）
        rationale: 判定理由说明
    """

    model_config = {"extra": "forbid"}

    participant_id: str = Field(
        description="参与者 ID",
    )
    dimension_id: str = Field(
        description="冲突维度 ID",
    )
    position: Literal["axis_a", "axis_b", "balanced", "neutral", "unknown"] = Field(
        description="立场位置",
    )
    strength: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="立场强度 (0.0-1.0)",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="置信度 (0.0-1.0)",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="支持该立场的证据",
    )
    rationale: Optional[str] = Field(
        default=None,
        max_length=500,
        description="判定理由说明",
    )

    def is_meaningful(self) -> bool:
        """
        判断是否有意义的立场信号

        只有当 position 不是 neutral/unknown 且 confidence 足够高时才有意义。

        Returns:
            bool: 是否有意义
        """
        return (
            self.position not in ("neutral", "unknown")
            and self.confidence >= 0.4
            and self.strength >= 0.3
        )

    def is_opposite_to(self, other: StanceSignal) -> bool:
        """
        判断与另一个立场信号是否对立

        要求在同一维度上，且立场在轴两端。

        Args:
            other: 另一个立场信号

        Returns:
            bool: 是否对立
        """
        if self.dimension_id != other.dimension_id:
            return False

        позиции = {self.position, other.position}
        return позиции == {"axis_a", "axis_b"}

    def is_aligned_with(self, other: StanceSignal) -> bool:
        """
        判断与另一个立场信号是否一致

        要求在同一维度上，且立场在同一端或都是 balanced。

        Args:
            other: 另一个立场信号

        Returns:
            bool: 是否一致
        """
        if self.dimension_id != other.dimension_id:
            return False

        if self.position == other.position:
            return True

        # balanced 与任何端都算部分对齐
        if self.position == "balanced" or other.position == "balanced":
            return True

        return False


__all__ = ["StanceSignal"]