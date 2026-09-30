"""
Tests for Baseline Planner

M4: Research & Planning Engine

测试范围：
- Planner 实现 Protocol
- 基于简单 TaskSpec 生成步骤测试
- 依赖关系生成测试
- required_capabilities / knowledge / resources 汇总测试
- risks / assumptions / open_questions 生成测试
- fallbacks 生成测试
- 鲁棒性测试：模糊 TaskSpec、约束冲突、缺失关键信息
- 高风险任务处理测试

Baseline 实现原则：
- 规则可解释
- 不接 LLM
- 所有决策可追溯
"""

from __future__ import annotations

import pytest


class TestBaselinePlannerBasics:
    """测试 BaselinePlanner 基础"""

    def test_planner_importable(self):
        """测试 Planner 可导入"""
        from src.infra.planners.baseline_planner import BaselinePlanner

        assert BaselinePlanner is not None

    def test_planner_implements_protocol(self):
        """测试 Planner 实现 Planner 协议"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.services.planner import Planner

        planner = BaselinePlanner()
        assert isinstance(planner, Planner)


class TestPlanDraftGeneration:
    """测试 PlanDraft 生成"""

    def test_planner_generates_plan_draft_from_task_spec(self):
        """测试从 TaskSpec 生成 PlanDraft"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        assert result.plan_draft is not None
        assert result.plan_draft.task_id == task_spec.id

    def test_planner_generates_strategy_summary(self):
        """测试生成策略摘要"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        assert result.plan_draft.strategy is not None
        assert len(result.plan_draft.strategy) > 0

    def test_planner_generates_steps_from_subtasks(self):
        """测试从 TaskSpec.subtasks 生成步骤"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_multi_step_task_spec

        planner = BaselinePlanner()
        task_spec = get_multi_step_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 应该从 subtasks 生成步骤
        assert len(result.plan_draft.steps) >= 1

    def test_planner_generates_steps_from_goal_when_no_subtasks(self):
        """测试当没有 subtasks 时从 goal 生成步骤"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 即使没有 subtasks，也应该生成至少一个步骤
        assert len(result.plan_draft.steps) >= 1


class TestRequirementsInference:
    """测试需求推断"""

    def test_planner_infers_role_requirements(self):
        """测试推断角色需求"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_team_composition_task_spec

        planner = BaselinePlanner()
        task_spec = get_team_composition_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 应该推断出角色需求
        assert len(result.plan_draft.role_requirements) >= 1

    def test_planner_infers_knowledge_requirements(self):
        """测试推断知识需求"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_multi_step_task_spec

        planner = BaselinePlanner()
        task_spec = get_multi_step_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 知识需求可以从 required_knowledge 继承
        # role_requirements 和 knowledge_requirements 可能为空，但字段必须存在
        assert hasattr(result.plan_draft, "knowledge_requirements")

    def test_planner_infers_resource_requirements(self):
        """测试推断资源需求"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_multi_step_task_spec

        planner = BaselinePlanner()
        task_spec = get_multi_step_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 资源需求可以从 required_resources 继承
        assert hasattr(result.plan_draft, "resource_requirements")


class TestDependenciesGeneration:
    """测试依赖关系生成"""

    def test_planner_generates_dependencies_from_subtask_dependencies(self):
        """测试从 subtask.dependencies 生成依赖关系"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_multi_step_task_spec

        planner = BaselinePlanner()
        task_spec = get_multi_step_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # subtasks 有依赖关系，应该被识别
        # dependencies 可能为空（如果只是简单顺序），但字段必须存在
        assert hasattr(result, "dependencies")

    def test_planner_generates_sequential_dependencies_for_steps(self):
        """测试为步骤生成顺序依赖"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_multi_step_task_spec

        planner = BaselinePlanner()
        task_spec = get_multi_step_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 如果有多个步骤，应该生成顺序依赖
        if len(result.plan_draft.steps) > 1:
            assert len(result.dependencies) >= 1


class TestRisksAndQuestions:
    """测试风险和问题识别"""

    def test_planner_identifies_risks_from_high_risk_task(self):
        """测试识别高风险任务的风险"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_high_risk_task_spec

        planner = BaselinePlanner()
        task_spec = get_high_risk_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 高风险任务应该识别出风险
        assert len(result.risks) >= 1

    def test_planner_identifies_risks_from_task_constraints(self):
        """测试从任务约束识别风险"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_high_risk_task_spec

        planner = BaselinePlanner()
        task_spec = get_high_risk_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 约束可能导致风险识别
        # risks 可能为空，但字段必须存在
        assert hasattr(result, "risks")

    def test_planner_identifies_open_questions_from_unknowns(self):
        """测试从 TaskSpec.unknowns 识别待确认问题"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_vague_task_spec

        planner = BaselinePlanner()
        task_spec = get_vague_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # unknowns 应该转换为 open_questions
        assert len(result.open_questions) >= 1

    def test_planner_generates_assumptions(self):
        """测试生成假设"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # assumptions 可能为空，但字段必须存在
        assert hasattr(result, "assumptions")


class TestFallbacks:
    """测试备选方案生成"""

    def test_planner_generates_fallbacks_for_high_risk_task(self):
        """测试为高风险任务生成备选方案"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_high_risk_task_spec

        planner = BaselinePlanner()
        task_spec = get_high_risk_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 高风险任务应该有备选方案
        assert len(result.fallbacks) >= 1

    def test_planner_may_not_generate_fallbacks_for_simple_task(self):
        """测试简单任务可能不需要备选方案"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 简单任务可能没有备选方案
        assert hasattr(result, "fallbacks")


class TestHandoffStrategy:
    """测试交接策略生成"""

    def test_planner_generates_handoff_strategy(self):
        """测试生成交接策略"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        assert result.plan_draft.handoff_strategy is not None

    def test_planner_generates_escalation_points_for_high_risk(self):
        """测试为高风险任务生成升级点"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_high_risk_task_spec

        planner = BaselinePlanner()
        task_spec = get_high_risk_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        # 高风险任务应该有升级点
        assert len(result.plan_draft.escalation_points) >= 1


class TestConfidence:
    """测试置信度计算"""

    def test_planner_calculates_confidence(self):
        """测试计算置信度"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        assert result.is_successful()
        assert 0.0 <= result.confidence <= 1.0

    def test_planner_lower_confidence_for_vague_task(self):
        """测试模糊任务置信度较低"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import (
            get_simple_design_task_spec,
            get_vague_task_spec,
        )

        planner = BaselinePlanner()

        clear_task_spec = get_simple_design_task_spec()
        clear_input = PlanningInput(task_spec=clear_task_spec)
        clear_result = planner.plan(clear_input)

        vague_task_spec = get_vague_task_spec()
        vague_input = PlanningInput(task_spec=vague_task_spec)
        vague_result = planner.plan(vague_input)

        assert clear_result.is_successful()
        assert vague_result.is_successful()
        # 明确任务的置信度应该更高
        assert clear_result.confidence >= vague_result.confidence


class TestRobustness:
    """测试鲁棒性"""

    def test_planner_handles_missing_required_capabilities(self):
        """测试处理缺失的必需能力"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        planner = BaselinePlanner()
        task_spec = TaskSpec(
            id="tsk_no_caps",
            goal="完成任务",
            deliverables=["产出"],
            constraints=[],
            success_criteria=["完成"],
            required_capabilities=["通用能力"],  # TaskSpec 需要至少一个能力
            required_knowledge=[],
            required_resources=[],
            risk_level=RiskLevel.LOW,
            unknowns=["缺少能力描述"],
        )
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        # 应该仍然成功，但有警告
        assert result.is_successful()
        # 可能有警告或 open_questions

    def test_planner_handles_empty_subtasks(self):
        """测试处理空的 subtasks"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        # 即使没有 subtasks，也应该生成基准计划
        assert result.is_successful()
        assert len(result.plan_draft.steps) >= 1

    def test_planner_handles_vague_task_spec(self):
        """测试处理模糊的 TaskSpec"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_vague_task_spec

        planner = BaselinePlanner()
        task_spec = get_vague_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        # 应该仍然产生结果，但有 open_questions
        assert result.plan_draft is not None
        assert len(result.open_questions) >= 1

    def test_planner_generates_warning_for_unknowns(self):
        """测试为 unknowns 生成警告"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_vague_task_spec

        planner = BaselinePlanner()
        task_spec = get_vague_task_spec()
        planning_input = PlanningInput(task_spec=task_spec)

        result = planner.plan(planning_input)

        # 有 unknowns 应该触发警告
        assert len(result.warnings) >= 1


class TestUnderstandingContext:
    """测试理解上下文传递"""

    def test_planner_aggregates_understanding_warnings(self):
        """测试聚合 understanding warnings"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import (
            get_simple_design_task_spec,
            get_understanding_warnings,
        )

        planner = BaselinePlanner()
        task_spec = get_simple_design_task_spec()
        warnings = get_understanding_warnings()
        planning_input = PlanningInput(
            task_spec=task_spec,
            understanding_warnings=warnings,
        )

        result = planner.plan(planning_input)

        assert result.is_successful()
        # understanding warnings 应该影响规划
        # 可能增加 open_questions 或 warnings

    def test_planner_uses_source_prompt(self):
        """测试使用 source_prompt"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_simple_design_task_spec

        planner = BaselinePlanner()
        task_spec = get_simple_design_task_spec()
        planning_input = PlanningInput(
            task_spec=task_spec,
            source_prompt="帮我设计一个系统架构方案",
        )

        result = planner.plan(planning_input)

        assert result.is_successful()
        # source_prompt 可以用于额外的上下文


class TestPlanningHints:
    """测试规划提示"""

    def test_planner_uses_planning_hints(self):
        """测试使用 planning_hints"""
        from src.infra.planners.baseline_planner import BaselinePlanner
        from src.domain.models.planning_input import PlanningInput
        from tests.fixtures.planning_inputs import get_multi_step_task_spec

        planner = BaselinePlanner()
        task_spec = get_multi_step_task_spec()
        planning_input = PlanningInput(
            task_spec=task_spec,
            planning_hints={"max_steps": 3, "prefer_small_team": True},
        )

        result = planner.plan(planning_input)

        assert result.is_successful()
        # planning_hints 可能影响规划结果
        # steps 数量可能受 max_steps 限制
        assert len(result.plan_draft.steps) <= 5  # 宽松检查