"""
Planner Protocol

M4: Research & Planning Engine

定义规划器的接口协议。

遵循 CLAUDE.md 的约束：
- 领域层定义接口，不依赖具体实现
- 所有决策必须可解释
- 先做 baseline，不做复杂智能规划
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.planning_input import PlanningInput
from src.domain.models.planning_result import PlanningResult


@runtime_checkable
class Planner(Protocol):
    """
    规划器协议

    定义将 TaskSpec 转换为 PlanningResult 的接口。
    实现必须：
    - 从 TaskSpec 生成最小可执行计划草案
    - 支持步骤拆解、依赖表达、能力/知识/资源需求汇总
    - 支持风险点、假设、待确认问题输出
    - 输出可解释的理据
    """

    def plan(self, input_data: PlanningInput) -> PlanningResult:
        """
        执行规划

        Args:
            input_data: 规划输入

        Returns:
            PlanningResult: 包含 PlanDraft 和规划元数据的结果
        """
        ...


__all__ = ["Planner"]