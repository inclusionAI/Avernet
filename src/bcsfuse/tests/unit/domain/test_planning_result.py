"""
Tests for Planning Result Model

M4: Research & Planning Engine

测试范围：
- PlanningResult 构造与字段校验
- PlanDraft 与 Schema 对齐
- 扩展字段验证：objective, dependencies, risks, assumptions, open_questions, fallbacks, status, confidence
- 辅助类型验证：PlanRisk, PlanFallback, DependencyRef, PlanningWarning, PlanningError
- is_successful() 方法测试
"""

from __future__ import annotations

import pytest


class TestPlanningResultBasics:
    """测试 PlanningResult 基础构造"""

    def test_planning_result_importable(self):
        """测试 PlanningResult 可导入"""
        from src.domain.models.planning_result import PlanningResult

        assert PlanningResult is not None

    def test_planning_result_constructs_with_plan_draft(self):
        """测试使用 PlanDraft 构造 PlanningResult"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="先调研再设计",
            steps=[
                PlanStep(
                    id="step_1",
                    title="调研",
                    objective="调研现有方案",
                ),
            ],
            role_requirements=["researcher"],
            knowledge_requirements=["相关技术"],
            resource_requirements=["文档工具"],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="完成技术调研",
        )

        assert result.plan_draft is not None
        assert result.plan_draft.task_id == "tsk_001"
        assert result.objective == "完成技术调研"

    def test_planning_result_accepts_optional_dependencies(self):
        """测试 PlanningResult 接受可选的 dependencies"""
        from src.domain.models.planning_result import (
            PlanningResult,
            DependencyRef,
        )
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="多步骤执行",
            steps=[
                PlanStep(id="step_1", title="步骤1", objective="目标1"),
                PlanStep(id="step_2", title="步骤2", objective="目标2"),
            ],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        dependencies = [
            DependencyRef(
                from_step="step_2",
                to_step="step_1",
                dependency_type="sequential",
            ),
        ]

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="多步骤任务",
            dependencies=dependencies,
        )

        assert len(result.dependencies) == 1
        assert result.dependencies[0].from_step == "step_2"

    def test_planning_result_accepts_optional_risks(self):
        """测试 PlanningResult 接受可选的 risks"""
        from src.domain.models.planning_result import (
            PlanningResult,
            PlanRisk,
        )
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        risks = [
            PlanRisk(
                risk_id="risk_1",
                description="资源不足",
                severity="medium",
                mitigation="准备备用资源",
            ),
        ]

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="风险评估任务",
            risks=risks,
        )

        assert len(result.risks) == 1
        assert result.risks[0].description == "资源不足"

    def test_planning_result_accepts_optional_assumptions(self):
        """测试 PlanningResult 接受可选的 assumptions"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="假设验证任务",
            assumptions=["团队能力充足", "时间充裕"],
        )

        assert len(result.assumptions) == 2
        assert "团队能力充足" in result.assumptions

    def test_planning_result_accepts_optional_open_questions(self):
        """测试 PlanningResult 接受可选的 open_questions"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="待确认任务",
            open_questions=["具体需求是什么?", "时间要求是什么?"],
        )

        assert len(result.open_questions) == 2

    def test_planning_result_accepts_optional_fallbacks(self):
        """测试 PlanningResult 接受可选的 fallbacks"""
        from src.domain.models.planning_result import (
            PlanningResult,
            PlanFallback,
        )
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        fallbacks = [
            PlanFallback(
                fallback_id="fb_1",
                trigger="主方案失败",
                action="切换到备用方案",
            ),
        ]

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="有备选的任务",
            fallbacks=fallbacks,
        )

        assert len(result.fallbacks) == 1
        assert result.fallbacks[0].trigger == "主方案失败"

    def test_planning_result_accepts_status_and_confidence(self):
        """测试 PlanningResult 接受 status 和 confidence"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="完整任务",
            status="ready",
            confidence=0.85,
        )

        assert result.status == "ready"
        assert result.confidence == 0.85


class TestPlanningResultWarningsAndErrors:
    """测试 PlanningResult 的 warnings 和 errors"""

    def test_planning_result_accepts_warnings(self):
        """测试 PlanningResult 接受 warnings"""
        from src.domain.models.planning_result import (
            PlanningResult,
            PlanningWarning,
        )
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        warnings = [
            PlanningWarning(
                field="steps",
                message="步骤较长可能影响执行效率",
                suggestion="考虑拆分步骤",
            ),
        ]

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="有警告的任务",
            warnings=warnings,
        )

        assert len(result.warnings) == 1

    def test_planning_result_accepts_errors(self):
        """测试 PlanningResult 接受 errors"""
        from src.domain.models.planning_result import (
            PlanningResult,
            PlanningError,
        )
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        errors = [
            PlanningError(
                field="required_capabilities",
                message="无法满足所需能力",
                severity="high",
            ),
        ]

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="有错误的任务",
            errors=errors,
        )

        assert len(result.errors) == 1


class TestPlanningResultIsSuccessful:
    """测试 is_successful() 方法"""

    def test_is_successful_returns_true_when_no_errors(self):
        """测试没有 errors 时 is_successful 返回 True"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="成功任务",
        )

        assert result.is_successful() is True

    def test_is_successful_returns_false_when_has_errors(self):
        """测试有 errors 时 is_successful 返回 False"""
        from src.domain.models.planning_result import (
            PlanningResult,
            PlanningError,
        )
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="失败任务",
            errors=[
                PlanningError(
                    field="planning",
                    message="规划失败",
                    severity="critical",
                ),
            ],
        )

        assert result.is_successful() is False

    def test_is_successful_returns_true_with_only_warnings(self):
        """测试只有 warnings 时 is_successful 返回 True"""
        from src.domain.models.planning_result import (
            PlanningResult,
            PlanningWarning,
        )
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="有警告但成功的任务",
            warnings=[
                PlanningWarning(
                    field="objective",
                    message="目标描述较长",
                    suggestion="简化描述",
                ),
            ],
        )

        assert result.is_successful() is True


class TestPlanningResultValidation:
    """测试 PlanningResult 验证"""

    def test_planning_result_requires_plan_draft(self):
        """测试 PlanningResult 必需 plan_draft"""
        from src.domain.models.planning_result import PlanningResult
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            PlanningResult(objective="缺少plan_draft")

        errors = exc_info.value.errors()
        assert any(e["loc"][0] == "plan_draft" for e in errors)

    def test_planning_result_requires_objective(self):
        """测试 PlanningResult 必需 objective"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep
        from pydantic import ValidationError

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        with pytest.raises(ValidationError) as exc_info:
            PlanningResult(plan_draft=plan_draft)

        errors = exc_info.value.errors()
        assert any(e["loc"][0] == "objective" for e in errors)

    def test_confidence_within_valid_range(self):
        """测试 confidence 在有效范围内"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        # 测试有效范围
        result1 = PlanningResult(
            plan_draft=plan_draft,
            objective="测试",
            confidence=0.0,
        )
        assert result1.confidence == 0.0

        result2 = PlanningResult(
            plan_draft=plan_draft.model_copy(deep=True),
            objective="测试",
            confidence=1.0,
        )
        assert result2.confidence == 1.0

        result3 = PlanningResult(
            plan_draft=plan_draft.model_copy(deep=True),
            objective="测试",
            confidence=0.5,
        )
        assert result3.confidence == 0.5

    def test_status_must_be_valid(self):
        """测试 status 必须是有效值"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        # 测试有效值
        for status in ["draft", "ready", "blocked"]:
            result = PlanningResult(
                plan_draft=plan_draft.model_copy(deep=True),
                objective="测试",
                status=status,
            )
            assert result.status == status

    def test_planning_result_rejects_extra_fields(self):
        """测试 PlanningResult 拒绝额外字段"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep
        from pydantic import ValidationError

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        with pytest.raises(ValidationError):
            PlanningResult(
                plan_draft=plan_draft,
                objective="测试",
                extra_field="not_allowed",
            )


class TestPlanningResultDefaults:
    """测试 PlanningResult 默认值"""

    def test_dependencies_defaults_to_empty_list(self):
        """测试 dependencies 默认为空列表"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="测试",
        )

        assert result.dependencies == []

    def test_risks_defaults_to_empty_list(self):
        """测试 risks 默认为空列表"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="测试",
        )

        assert result.risks == []

    def test_assumptions_defaults_to_empty_list(self):
        """测试 assumptions 默认为空列表"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="测试",
        )

        assert result.assumptions == []

    def test_open_questions_defaults_to_empty_list(self):
        """测试 open_questions 默认为空列表"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="测试",
        )

        assert result.open_questions == []

    def test_fallbacks_defaults_to_empty_list(self):
        """测试 fallbacks 默认为空列表"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="测试",
        )

        assert result.fallbacks == []

    def test_warnings_defaults_to_empty_list(self):
        """测试 warnings 默认为空列表"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="测试",
        )

        assert result.warnings == []

    def test_errors_defaults_to_empty_list(self):
        """测试 errors 默认为空列表"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="测试",
        )

        assert result.errors == []

    def test_status_defaults_to_draft(self):
        """测试 status 默认为 draft"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="测试",
        )

        assert result.status == "draft"

    def test_confidence_defaults_to_zero(self):
        """测试 confidence 默认为 0.0"""
        from src.domain.models.planning_result import PlanningResult
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        plan_draft = PlanDraft(
            task_id="tsk_001",
            strategy="执行计划",
            steps=[PlanStep(id="step_1", title="步骤", objective="目标")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )

        result = PlanningResult(
            plan_draft=plan_draft,
            objective="测试",
        )

        assert result.confidence == 0.0


class TestHelperTypes:
    """测试辅助类型"""

    def test_plan_risk_constructs(self):
        """测试 PlanRisk 构造"""
        from src.domain.models.planning_result import PlanRisk

        risk = PlanRisk(
            risk_id="risk_1",
            description="测试风险",
            severity="high",
            mitigation="测试缓解措施",
        )

        assert risk.risk_id == "risk_1"
        assert risk.description == "测试风险"
        assert risk.severity == "high"
        assert risk.mitigation == "测试缓解措施"

    def test_plan_fallback_constructs(self):
        """测试 PlanFallback 构造"""
        from src.domain.models.planning_result import PlanFallback

        fallback = PlanFallback(
            fallback_id="fb_1",
            trigger="测试触发条件",
            action="测试备选动作",
        )

        assert fallback.fallback_id == "fb_1"
        assert fallback.trigger == "测试触发条件"
        assert fallback.action == "测试备选动作"

    def test_dependency_ref_constructs(self):
        """测试 DependencyRef 构造"""
        from src.domain.models.planning_result import DependencyRef

        dep = DependencyRef(
            from_step="step_2",
            to_step="step_1",
            dependency_type="sequential",
        )

        assert dep.from_step == "step_2"
        assert dep.to_step == "step_1"
        assert dep.dependency_type == "sequential"

    def test_planning_warning_constructs(self):
        """测试 PlanningWarning 构造"""
        from src.domain.models.planning_result import PlanningWarning

        warning = PlanningWarning(
            field="test_field",
            message="测试警告",
            suggestion="测试建议",
        )

        assert warning.field == "test_field"
        assert warning.message == "测试警告"
        assert warning.suggestion == "测试建议"

    def test_planning_error_constructs(self):
        """测试 PlanningError 构造"""
        from src.domain.models.planning_result import PlanningError

        error = PlanningError(
            field="test_field",
            message="测试错误",
            severity="critical",
        )

        assert error.field == "test_field"
        assert error.message == "测试错误"
        assert error.severity == "critical"