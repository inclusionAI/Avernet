"""
Retrieval Mode

Worker Profile Retrieval & Fusion Simulation Baseline

历史画像检索/评分场景枚举，不代表四套现役搜索引擎。

当前边界（2026-10-09）：
- POST /api/v1/recommend 固定传 EXPERT_DIAGNOSIS；正常搜索主链路是
  WorkerVectorMatchService 的片段向量召回 + 关键词召回 + RRF + 可选精排。
- 下列枚举仍用于旧 ModeAwareScorer 的权重以及兼容画像检索分支。
  AGENT 的损坏 Phase E 混合检索实验已移除；旧开关开启时直接保留
  原来的兜底评分，不重新启用实验，也不改变现役搜索算法。
- FusionMode 是独立的融合业务契约，另含 bot_profile_fuse；不能因搜索
  收敛为一条主链路，就连带删除融合模式或在搜索中新增对应算法分支。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal


class RetrievalMode(str, Enum):
    """
    检索模式枚举

    .. deprecated:: 2026-10-09
       按模式选择搜索算法的设计已弃用；仅保留场景标记和旧调用兼容。
       新搜索调用使用 /api/v1/recommend 的统一链路，不新增模式分支。
       EXPERT_DIAGNOSIS 仍是当前接口使用的标记，不应直接删除；
       此弃用说明不适用于独立的 FusionMode 业务契约。

    历史上对应部分融合场景，保留用于兼容，不是搜索 API 的模式选项：
    - agent: G1 专家咨询模式
    - conflict_alignment: G2 冲突对齐模式
    - expert_diagnosis: G5 专家诊断模式
    - general: 内部通用检索模式（非 fusion 对外接口）
    """

    AGENT = "agent"  # DEPRECATED: 历史检索兼容值，不用于新增搜索路径。
    CONFLICT_ALIGNMENT = "conflict_alignment"  # DEPRECATED: 历史检索兼容值。
    EXPERT_DIAGNOSIS = "expert_diagnosis"  # 当前统一搜索仍使用的兼容场景标记。
    GENERAL = "general"  # DEPRECATED: 历史检索兼容值。

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
