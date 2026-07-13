"""
Tests for Planning Service

M4: Research & Planning Engine

测试范围：
- PlanningService 基础构造
- 从 TaskSpec 生成 PlanningResult
- 警告/错误聚合测试
- 与 Planner 的协调测试

Service 职责：
- 接收输入
- 调用 planner
- 汇总 warnings / errors
- 输出 PlanningResult
"""

from __future__ import annotations

import pytest


class TestPlanningServiceBasics:
    """测试 PlanningService 基础"""

    def test_planning_service_importable(self):
        """测试 PlanningService 可导入"""
        from src.application.services.planning_service import PlanningService

        assert PlanningService is not None

    def test_planning_service_constructs_with_planner(self):
        """测试使用 planner 构造 PlanningService"""
        from src.application.services.planning_service import PlanningService
        from src.infra.planners.baseline_planner import BaselinePlanner

        planner = BaselinePlanner()
        service = PlanningService(planner=planner)

        assert service is not None


class TestPlanningServiceExecution:
    """测试 PlanningService 执行"""

    def test_service_plans_from_task_spec(self):
        """测试从 TaskSpec 生成计划"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        service = PlanningService(planner=planner)

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = service.plan(planning_input)

        assert result.is_successful()
        assert result.plan_draft is not None
        assert result.plan_draft.task_id == task_spec.id

    def test_service_aggregates_understanding_warnings(self):
        """测试聚合 understanding warnings"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import (
            get_simple_design_task_spec,
            get_understanding_warnings,
        )

        planner = BaselinePlanner()
        service = PlanningService(planner=planner)

        task_spec = get_simple_design_task_spec()
        warnings = get_understanding_warnings()
        planning_input = PlanningInput(
            task_spec=task_spec,
            understanding_warnings=warnings,
        )

        result = service.plan(planning_input)

        assert result.is_successful()
        # understanding warnings 应该被包含在结果中

    def test_service_propagates_planning_errors(self):
        """测试传播 planning errors"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        service = PlanningService(planner=planner)

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = service.plan(planning_input)

        # 正常情况下不应该有 errors
        assert result.is_successful()
        assert len(result.errors) == 0

    def test_service_returns_successful_result(self):
        """测试返回成功的结果"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import get_multi_step_task_spec

        planner = BaselinePlanner()
        service = PlanningService(planner=planner)

        task_spec = get_multi_step_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = service.plan(planning_input)

        assert result.is_successful()
        assert result.plan_draft is not None
        assert len(result.plan_draft.steps) >= 1


class TestPlanningServiceInputHandling:
    """测试 PlanningService 输入处理"""

    def test_service_handles_high_risk_task(self):
        """测试处理高风险任务"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import get_high_risk_task_spec

        planner = BaselinePlanner()
        service = PlanningService(planner=planner)

        task_spec = get_high_risk_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = service.plan(planning_input)

        assert result.is_successful()
        # 高风险任务应该有更多风险和备选方案
        assert len(result.risks) >= 1

    def test_service_handles_vague_task(self):
        """测试处理模糊任务"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import get_vague_task_spec

        planner = BaselinePlanner()
        service = PlanningService(planner=planner)

        task_spec = get_vague_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = service.plan(planning_input)

        # 应该仍然产生结果，但有 open_questions
        assert result.plan_draft is not None
        assert len(result.open_questions) >= 1

    def test_service_handles_task_with_subtasks(self):
        """测试处理有子任务的任务"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import get_multi_step_task_spec

        planner = BaselinePlanner()
        service = PlanningService(planner=planner)

        task_spec = get_multi_step_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = service.plan(planning_input)

        assert result.is_successful()
        # 子任务应该被转换为步骤
        assert len(result.plan_draft.steps) >= len(task_spec.subtasks)


class TestPlanningServiceResultSummary:
    """测试 PlanningService 结果摘要"""

    def test_service_result_has_summary(self):
        """测试结果有摘要方法"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        service = PlanningService(planner=planner)

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = service.plan(planning_input)

        summary = result.get_summary()

        assert summary is not None
        assert "task_id" in summary
        assert "objective" in summary
        assert "status" in summary
        assert "confidence" in summary
        assert "is_successful" in summary