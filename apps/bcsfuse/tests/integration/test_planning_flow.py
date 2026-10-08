"""
Tests for Planning Flow Integration

M4: Research & Planning Engine

测试范围：
- 从 TaskSpec 输入到 PlanDraft 输出的最小闭环测试
- 与 M3 Task Understanding 的集成
- 端到端规划流程测试

集成测试原则：
- 使用真实组件（非 mock）
- 验证模块间协作
- 覆盖主 happy path 和关键失败路径
"""

from __future__ import annotations

import pytest


class TestPlanningFlowIntegration:
    """测试规划流程集成"""

    def test_task_spec_to_plan_draft_flow(self):
        """测试从 TaskSpec 到 PlanDraft 的完整流程"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.task_spec import TaskSpec, RiskLevel, Subtask

        # 准备输入
        task_spec = TaskSpec(
            id="tsk_integration_001",
            goal="完成一个完整的技术调研项目",
            deliverables=["调研报告", "技术选型文档", "实施方案"],
            constraints=["一周内完成", "预算有限", "需要审批"],
            success_criteria=["报告完成", "选型确定", "方案可行"],
            required_capabilities=["信息检索", "分析能力", "文档编写"],
            required_knowledge=["相关技术领域", "业务背景"],
            required_resources=["调研资料", "文档工具", "审批流程"],
            risk_level=RiskLevel.MEDIUM,
            unknowns=["具体调研深度"],
            subtasks=[
                Subtask(
                    id="sub_1",
                    title="信息收集",
                    objective="收集相关技术资料",
                    dependencies=[],
                ),
                Subtask(
                    id="sub_2",
                    title="分析对比",
                    objective="分析和对比各技术方案",
                    dependencies=["sub_1"],
                ),
                Subtask(
                    id="sub_3",
                    title="编写报告",
                    objective="编写调研报告和选型文档",
                    dependencies=["sub_2"],
                ),
            ],
            source_prompt="帮我完成一个技术调研项目",
        )

        planning_input = PlanningInput(
            task_spec=task_spec,
            source_prompt="帮我完成一个技术调研项目",
            planning_hints={"prefer_concise": True},
        )

        # 执行规划
        planner = BaselinePlanner()
        service = PlanningService(planner=planner)
        result = service.plan(planning_input)

        # 验证结果
        assert result.is_successful(), f"Planning should succeed, but got errors: {result.errors}"
        assert result.plan_draft is not None
        assert result.plan_draft.task_id == "tsk_integration_001"

        # 验证 PlanDraft 核心字段
        assert len(result.plan_draft.steps) >= 1
        assert len(result.plan_draft.role_requirements) >= 1
        assert result.plan_draft.handoff_strategy is not None

        # 验证扩展字段
        assert result.objective == task_spec.goal
        assert len(result.open_questions) >= 1  # 因为有 unknowns
        assert 0.0 <= result.confidence <= 1.0

    def test_high_risk_task_planning_flow(self):
        """测试高风险任务的规划流程"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        # 准备高风险任务
        task_spec = TaskSpec(
            id="tsk_high_risk_001",
            goal="修改生产环境数据库配置",
            deliverables=["配置变更报告", "验证结果"],
            constraints=["必须审批", "需要回滚方案", "生产环境"],
            success_criteria=["配置更新成功", "验证通过", "无故障"],
            required_capabilities=["数据库管理", "运维能力", "问题排查"],
            required_knowledge=["生产环境架构", "数据库配置"],
            required_resources=["生产数据库访问权限", "审批权限"],
            risk_level=RiskLevel.HIGH,
            unknowns=["当前配置状态"],
            subtasks=[],
            source_prompt="修改生产数据库配置，需要审批",
        )

        planning_input = PlanningInput(task_spec=task_spec)

        # 执行规划
        planner = BaselinePlanner()
        service = PlanningService(planner=planner)
        result = service.plan(planning_input)

        # 验证高风险任务的特征
        assert result.is_successful()
        assert len(result.risks) >= 1, "High risk task should have risks"
        assert len(result.fallbacks) >= 1, "High risk task should have fallbacks"
        assert len(result.plan_draft.escalation_points) >= 1, "High risk task should have escalation points"
        assert result.plan_draft.handoff_strategy == "manual_approval", "High risk task should require manual approval"

    def test_vague_task_planning_flow(self):
        """测试模糊任务的规划流程"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        # 准备模糊任务
        task_spec = TaskSpec(
            id="tsk_vague_001",
            goal="做一个系统",
            deliverables=["任务产出"],
            constraints=[],
            success_criteria=["任务目标已达成"],
            required_capabilities=["通用能力"],
            required_knowledge=[],
            required_resources=[],
            risk_level=RiskLevel.LOW,
            unknowns=[
                "任务描述过于简短，具体范围不明确",
                "未指定时间限制",
                "未指定约束条件",
            ],
            subtasks=[],
            source_prompt="做一个系统",
        )

        planning_input = PlanningInput(task_spec=task_spec)

        # 执行规划
        planner = BaselinePlanner()
        service = PlanningService(planner=planner)
        result = service.plan(planning_input)

        # 验证模糊任务的处理
        assert result.plan_draft is not None
        assert len(result.open_questions) >= 1, "Vague task should have open questions"
        assert len(result.warnings) >= 1, "Vague task should have warnings"
        assert result.confidence < 1.0, "Vague task should have lower confidence"

    def test_planning_with_understanding_context(self):
        """测试带有理解上下文的规划流程"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.task_spec import TaskSpec, RiskLevel
        from src.domain.models.task_understanding_result import UnderstandingWarning

        # 准备输入
        task_spec = TaskSpec(
            id="tsk_context_001",
            goal="优化系统性能",
            deliverables=["性能优化报告", "优化方案"],
            constraints=["不能影响业务连续性"],
            success_criteria=["性能提升20%", "无故障"],
            required_capabilities=["性能分析", "系统优化"],
            required_knowledge=["系统架构"],
            required_resources=["性能监控工具"],
            risk_level=RiskLevel.MEDIUM,
            unknowns=["当前性能基线"],
            subtasks=[],
            source_prompt="优化系统性能，不要影响业务",
        )

        understanding_warnings = [
            UnderstandingWarning(
                field="unknowns",
                message="具体优化目标不够明确",
                suggestion="建议明确优化指标和目标值",
            ),
        ]

        planning_input = PlanningInput(
            task_spec=task_spec,
            understanding_warnings=understanding_warnings,
            source_prompt="优化系统性能，不要影响业务",
        )

        # 执行规划
        planner = BaselinePlanner()
        service = PlanningService(planner=planner)
        result = service.plan(planning_input)

        # 验证上下文被正确处理
        assert result.is_successful()
        # understanding_warnings 应该影响规划的 warnings
        assert any("understanding" in w.field for w in result.warnings) or len(result.open_questions) >= 1

    def test_planning_result_can_be_summarized(self):
        """测试规划结果可以生成摘要"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import get_multi_step_task_spec

        task_spec = get_multi_step_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        # 执行规划
        planner = BaselinePlanner()
        service = PlanningService(planner=planner)
        result = service.plan(planning_input)

        # 获取摘要
        summary = result.get_summary()

        # 验证摘要内容
        assert summary["task_id"] == task_spec.id
        assert summary["objective"] is not None
        assert summary["status"] in ["draft", "ready", "blocked"]
        assert summary["is_successful"] is True
        assert summary["steps_count"] >= 1
        assert summary["risks_count"] >= 0
        assert summary["warnings_count"] >= 0
        assert summary["errors_count"] == 0


class TestPlanningFlowValidation:
    """测试规划流程验证"""

    def test_plan_draft_conforms_to_schema(self):
        """测试 PlanDraft 符合 Schema"""
        import json
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import get_simple_design_task_spec
        from src.infra.schema_loader import validate_with_store
        import jsonschema

        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        # 执行规划
        planner = BaselinePlanner()
        service = PlanningService(planner=planner)
        result = service.plan(planning_input)

        # 验证 PlanDraft 符合 Schema
        plan_draft_dict = result.plan_draft.model_dump(mode='json')

        try:
            validate_with_store(instance=plan_draft_dict, schema_name="PlanDraft.json")
        except jsonschema.ValidationError as e:
            pytest.fail(f"PlanDraft does not conform to schema: {e.message}")

    def test_planning_preserves_task_relationship(self):
        """测试规划保持任务关联"""
        from src.application.services.planning_service import PlanningService
        from src.domain.models.planning_input import PlanningInput
        from src.infra.planners.baseline_planner import BaselinePlanner
        from tests.fixtures.planning_inputs import get_multi_step_task_spec

        task_spec = get_multi_step_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        # 执行规划
        planner = BaselinePlanner()
        service = PlanningService(planner=planner)
        result = service.plan(planning_input)

        # 验证任务关联保持
        assert result.plan_draft.task_id == task_spec.id
        assert result.objective == task_spec.goal

        # 验证步骤数量关系
        if task_spec.subtasks:
            assert len(result.plan_draft.steps) >= 1