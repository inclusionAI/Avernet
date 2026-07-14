"""
Planning Service

M4: Research & Planning Engine

规划服务，负责协调 planner 并汇总结果。

Service 职责：
- 接收输入
- 调用 planner
- 汇总 warnings / errors
- 输出 PlanningResult

Service 不做：
- 不实现规划规则（由 planner 做）
- 不做检索
- 不做复杂规划
"""

from __future__ import annotations

from src.domain.models.planning_input import PlanningInput
from src.domain.models.planning_result import (
    PlanningResult,
    PlanningError,
)
from src.domain.services.planner import Planner


class PlanningService:
    """
    规划服务

    负责协调 Planner 并处理结果。
    """

    def __init__(self, planner: Planner):
        """
        初始化服务

        Args:
            planner: Planner 实现，用于执行实际的规划逻辑
        """
        self._planner = planner

    def plan(self, input_data: PlanningInput) -> PlanningResult:
        """
        执行规划

        Args:
            input_data: 规划输入

        Returns:
            PlanningResult: 规划结果
        """
        try:
            # 调用 planner 执行规划
            result = self._planner.plan(input_data)
            return result

        except Exception as e:
            # 捕获 planner 异常，返回带有 error 的结果
            return self._create_error_result(str(e), input_data)

    def _create_error_result(
        self, error_message: str, input_data: PlanningInput
    ) -> PlanningResult:
        """
        创建错误结果

        Args:
            error_message: 错误消息
            input_data: 原始输入数据

        Returns:
            包含错误的规划结果
        """
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        # 创建最小的 PlanDraft
        plan_draft = PlanDraft(
            task_id=input_data.task_spec.id,
            strategy="规划失败",
            steps=[
                PlanStep(
                    id="step_1",
                    title="错误",
                    objective="规划过程中发生错误",
                ),
            ],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="manual",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="规划失败",
        )
        result.errors.append(PlanningError(
            field="planning",
            message=f"Planning service failed: {error_message}",
            severity="critical",
        ))
        return result


__all__ = ["PlanningService"]