"""
Retrieval Mode

Worker Profile Retrieval & Fusion Simulation Baseline

检索模式枚举，对齐 fusion_mode。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal


class RetrievalMode(str, Enum):
    """
    检索模式枚举

    对齐 FusionRequest.fusion_mode 的值：
    - agent: G1 专家咨询模式
    - conflict_alignment: G2 冲突对齐模式
    - expert_diagnosis: G5 专家诊断模式
    - general: 内部通用检索模式（非 fusion 对外接口）
    """

    AGENT = "agent"
    CONFLICT_ALIGNMENT = "conflict_alignment"
    EXPERT_DIAGNOSIS = "expert_diagnosis"
    GENERAL = "general"

    @classmethod
    def fusion_modes(cls) -> list["RetrievalMode"]:
        """
        获取 fusion 相关的模式列表

        Returns:
            fusion 模式列表（不含 general）
        """
        return [
            cls.AGENT,
            cls.CONFLICT_ALIGNMENT,
            cls.EXPERT_DIAGNOSIS,
        ]

    @classmethod
    def from_fusion_mode(cls, fusion_mode: str) -> "RetrievalMode":
        """
        从 fusion_mode 字符串转换

        Args:
            fusion_mode: fusion_mode 字符串值

        Returns:
            RetrievalMode 枚举值

        Raises:
            ValueError: 无效的 fusion_mode
        """
        mode_map = {
            "agent": cls.AGENT,
            "conflict_alignment": cls.CONFLICT_ALIGNMENT,
            "expert_diagnosis": cls.EXPERT_DIAGNOSIS,
        }
        if fusion_mode not in mode_map:
            raise ValueError(f"Invalid fusion_mode: {fusion_mode}")
        return mode_map[fusion_mode]


# 类型别名，用于类型注解
FusionModeLiteral = Literal["agent", "conflict_alignment", "expert_diagnosis"]


__all__ = [
    "RetrievalMode",
    "FusionModeLiteral",
]