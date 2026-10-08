"""
Tests for Task Understanding Result

M3: Task Understanding Engine

测试范围：
- TaskUnderstandingResult 创建与字段校验
- warnings / errors 聚合测试
- TaskSpec 关联测试
- 部分成功场景测试

输出模型职责：
- 封装理解结果
- 包含 TaskSpec
- 包含 warnings / errors
- 支持部分成功
"""

from __future__ import annotations

import pytest


class TestTaskUnderstandingResultBasics:
    """测试 TaskUnderstandingResult 基本"""

    def test_result_importable(self):
        """测试 Result 可导入"""
        from src.domain.models.task_understanding_result import TaskUnderstandingResult

        assert TaskUnderstandingResult is not None

    def test_create_result_with_task_spec(self):
        """测试创建带 TaskSpec 的结果"""
        from src.domain.models.task_understanding_result import TaskUnderstandingResult
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        task_spec = TaskSpec(
            id="tsk_test_001",
            goal="完成系统方案设计",
            deliverables=["系统方案文档"],
            success_criteria=["方案通过评审"],
            required_capabilities=["系统设计"],
            risk_level=RiskLevel.LOW,
        )

        result = TaskUnderstandingResult(task_spec=task_spec)

        assert result.task_spec is not None
        assert result.task_spec.goal == "完成系统方案设计"
        assert result.is_successful()

    def test_result_has_source_reference(self):
        """测试结果包含来源引用"""
        from src.domain.models.task_understanding_result import TaskUnderstandingResult
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        task_spec = TaskSpec(
            id="tsk_test_002",
            goal="Test goal",
            deliverables=["Test deliverable"],
            success_criteria=["Test criteria"],
            required_capabilities=["Test capability"],
            risk_level=RiskLevel.LOW,
        )

        result = TaskUnderstandingResult(
            task_spec=task_spec,
            source_prompt="Original user request",
        )

        assert result.source_prompt == "Original user request"


class TestTaskUnderstandingResultWarnings:
    """测试 warnings 处理"""

    def test_warnings_default_to_empty_list(self):
        """测试 warnings 默认空列表"""
        from src.domain.models.task_understanding_result import TaskUnderstandingResult
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        task_spec = TaskSpec(
            id="tsk_test_003",
            goal="Test goal",
            deliverables=["Test"],
            success_criteria=["Test"],
            required_capabilities=["Test"],
            risk_level=RiskLevel.LOW,
        )

        result = TaskUnderstandingResult(task_spec=task_spec)

        assert result.warnings == []

    def test_can_add_warnings(self):
        """测试可以添加 warnings"""
        from src.domain.models.task_understanding_result import (
            TaskUnderstandingResult,
            UnderstandingWarning,
        )
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        task_spec = TaskSpec(
            id="tsk_test_004",
            goal="Test goal",
            deliverables=["Test"],
            success_criteria=["Test"],
            required_capabilities=["Test"],
            risk_level=RiskLevel.LOW,
        )

        result = TaskUnderstandingResult(task_spec=task_spec)
        result.warnings.append(UnderstandingWarning(
            field="goal",
            message="Goal is vague",
            suggestion="Please provide more specific goal",
        ))

        assert len(result.warnings) == 1
        assert result.warnings[0].field == "goal"

    def test_result_with_warnings_is_still_successful(self):
        """测试有 warnings 的结果仍算成功"""
        from src.domain.models.task_understanding_result import (
            TaskUnderstandingResult,
            UnderstandingWarning,
        )
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        task_spec = TaskSpec(
            id="tsk_test_005",
            goal="Test goal",
            deliverables=["Test"],
            success_criteria=["Test"],
            required_capabilities=["Test"],
            risk_level=RiskLevel.LOW,
        )

        result = TaskUnderstandingResult(task_spec=task_spec)
        result.warnings.append(UnderstandingWarning(
            field="context",
            message="Context not provided",
        ))

        # 有 warnings 但无 errors，仍算成功
        assert result.is_successful()


class TestTaskUnderstandingResultErrors:
    """测试 errors 处理"""

    def test_errors_default_to_empty_list(self):
        """测试 errors 默认空列表"""
        from src.domain.models.task_understanding_result import TaskUnderstandingResult
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        task_spec = TaskSpec(
            id="tsk_test_006",
            goal="Test goal",
            deliverables=["Test"],
            success_criteria=["Test"],
            required_capabilities=["Test"],
            risk_level=RiskLevel.LOW,
        )

        result = TaskUnderstandingResult(task_spec=task_spec)

        assert result.errors == []

    def test_result_with_errors_is_not_successful(self):
        """测试有 errors 的结果不算成功"""
        from src.domain.models.task_understanding_result import (
            TaskUnderstandingResult,
            UnderstandingError,
        )
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        task_spec = TaskSpec(
            id="tsk_test_007",
            goal="Test goal",
            deliverables=["Test"],
            success_criteria=["Test"],
            required_capabilities=["Test"],
            risk_level=RiskLevel.LOW,
        )

        result = TaskUnderstandingResult(task_spec=task_spec)
        result.errors.append(UnderstandingError(
            field="raw_request",
            message="Request is too ambiguous",
        ))

        # 有 errors 则不算成功
        assert not result.is_successful()

    def test_can_add_errors(self):
        """测试可以添加 errors"""
        from src.domain.models.task_understanding_result import (
            TaskUnderstandingResult,
            UnderstandingError,
        )
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        task_spec = TaskSpec(
            id="tsk_test_008",
            goal="Test goal",
            deliverables=["Test"],
            success_criteria=["Test"],
            required_capabilities=["Test"],
            risk_level=RiskLevel.LOW,
        )

        result = TaskUnderstandingResult(task_spec=task_spec)
        result.errors.append(UnderstandingError(
            field="deliverables",
            message="No deliverables could be extracted",
            severity="high",
        ))

        assert len(result.errors) == 1


class TestUnderstandingWarning:
    """测试 UnderstandingWarning"""

    def test_create_warning_with_all_fields(self):
        """测试创建完整 warning"""
        from src.domain.models.task_understanding_result import UnderstandingWarning

        warning = UnderstandingWarning(
            field="goal",
            message="Goal is too vague",
            suggestion="Please provide a more specific goal",
        )

        assert warning.field == "goal"
        assert warning.message == "Goal is too vague"
        assert warning.suggestion == "Please provide a more specific goal"

    def test_create_warning_minimal(self):
        """测试创建最小 warning"""
        from src.domain.models.task_understanding_result import UnderstandingWarning

        warning = UnderstandingWarning(
            field="context",
            message="Context is missing",
        )

        assert warning.field == "context"
        assert warning.suggestion is None


class TestUnderstandingError:
    """测试 UnderstandingError"""

    def test_create_error_with_all_fields(self):
        """测试创建完整 error"""
        from src.domain.models.task_understanding_result import UnderstandingError

        error = UnderstandingError(
            field="raw_request",
            message="Request is empty",
            severity="critical",
        )

        assert error.field == "raw_request"
        assert error.message == "Request is empty"
        assert error.severity == "critical"

    def test_error_default_severity(self):
        """测试 error 默认严重级别"""
        from src.domain.models.task_understanding_result import UnderstandingError

        error = UnderstandingError(
            field="goal",
            message="Goal extraction failed",
        )

        assert error.severity == "medium"


class TestTaskUnderstandingResultPartialSuccess:
    """测试部分成功场景"""

    def test_partial_success_with_unknowns(self):
        """测试有 unknowns 的部分成功"""
        from src.domain.models.task_understanding_result import TaskUnderstandingResult
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        task_spec = TaskSpec(
            id="tsk_test_009",
            goal="Make a system",
            deliverables=["System"],
            success_criteria=["System works"],
            required_capabilities=["Development"],
            risk_level=RiskLevel.MEDIUM,
            unknowns=["System scope unclear", "Technology stack not specified"],
        )

        result = TaskUnderstandingResult(task_spec=task_spec)

        # 有 unknowns 但无 errors，仍算成功
        assert result.is_successful()
        assert len(task_spec.unknowns) == 2

    def test_result_without_task_spec_is_error(self):
        """测试无 TaskSpec 的结果是错误"""
        from src.domain.models.task_understanding_result import TaskUnderstandingResult

        # TaskUnderstandingResult 必须有 task_spec
        # 这个测试验证 None task_spec 的行为
        result = TaskUnderstandingResult(task_spec=None)

        # 没有 task_spec 应该不算成功
        assert not result.is_successful()


class TestTaskUnderstandingResultSummary:
    """测试结果摘要"""

    def test_get_summary(self):
        """测试获取结果摘要"""
        from src.domain.models.task_understanding_result import TaskUnderstandingResult
        from src.domain.models.task_spec import TaskSpec, RiskLevel

        task_spec = TaskSpec(
            id="tsk_test_010",
            goal="Complete the project",
            deliverables=["Report"],
            success_criteria=["Done"],
            required_capabilities=["Writing"],
            risk_level=RiskLevel.LOW,
        )

        result = TaskUnderstandingResult(task_spec=task_spec)
        summary = result.get_summary()

        assert "task_id" in summary
        assert "goal" in summary
        assert "warnings_count" in summary
        assert "errors_count" in summary