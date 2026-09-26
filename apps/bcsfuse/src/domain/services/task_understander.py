"""
Task Understander Protocol

M3: Task Understanding Engine

定义任务理解器的接口协议。

遵循 CLAUDE.md 的约束：
- 领域层定义接口，不依赖具体实现
- 所有决策必须可解释
- 先做 baseline，不做复杂智能规划
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.task_understanding_input import TaskUnderstandingInput
from src.domain.models.task_understanding_result import TaskUnderstandingResult


@runtime_checkable
class TaskUnderstander(Protocol):
    """
    任务理解器协议

    定义将用户输入转换为 TaskSpec 的接口。
    实现必须：
    - 支持从自然语言中做基础任务归一化
    - 支持抽取显式与弱显式约束
    - 支持识别缺失信息并输出 unknowns
    - 支持最小粒度的子任务拆解
    - 输出可解释的理据
    """

    def understand(self, input_data: TaskUnderstandingInput) -> TaskUnderstandingResult:
        """
        理解任务并生成 TaskSpec

        Args:
            input_data: 任务理解输入

        Returns:
            TaskUnderstandingResult: 包含 TaskSpec、warnings 和 errors
        """
        ...


__all__ = ["TaskUnderstander"]