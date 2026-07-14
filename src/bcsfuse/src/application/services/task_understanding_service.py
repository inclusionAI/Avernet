"""
Task Understanding Service

M3: Task Understanding Engine

任务理解服务，负责协调 understander 并汇总结果。

Service 职责：
- 接收输入
- 调用 understander
- 汇总 unknowns / warnings / errors
- 输出 TaskSpec 或 understanding result

Service 不做：
- 不解析自然语言（由 understander 做）
- 不实现抽取规则（由 understander 做）
- 不做复杂规划
- 不做检索
"""

from __future__ import annotations

from src.domain.models.task_understanding_input import TaskUnderstandingInput
from src.domain.models.task_understanding_result import (
    TaskUnderstandingResult,
    UnderstandingError,
)
from src.domain.services.task_understander import TaskUnderstander


class TaskUnderstandingService:
    """
    任务理解服务

    负责协调 TaskUnderstander 并处理结果。
    """

    def __init__(self, understander: TaskUnderstander):
        """
        初始化服务

        Args:
            understander: TaskUnderstander 实现，用于执行实际的理解逻辑
        """
        self._understander = understander

    def understand(self, input_data: TaskUnderstandingInput) -> TaskUnderstandingResult:
        """
        执行任务理解

        Args:
            input_data: 任务理解输入

        Returns:
            TaskUnderstandingResult: 理解结果
        """
        try:
            # 调用 understander 执行理解
            result = self._understander.understand(input_data)
            return result

        except Exception as e:
            # 捕获 understander 异常，返回带有 error 的结果
            return self._create_error_result(str(e), input_data)

    def _create_error_result(
        self, error_message: str, input_data: TaskUnderstandingInput
    ) -> TaskUnderstandingResult:
        """
        创建错误结果

        Args:
            error_message: 错误消息
            input_data: 原始输入数据

        Returns:
            包含错误的理解结果
        """
        result = TaskUnderstandingResult(source_prompt=input_data.raw_request)
        result.errors.append(UnderstandingError(
            field="understanding",
            message=f"Task understanding failed: {error_message}",
            severity="critical",
        ))
        return result


__all__ = ["TaskUnderstandingService"]