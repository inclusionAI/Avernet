"""
Tests for OpenClawAdapterResult Domain Model

M9: OpenClaw Adapter

测试 OpenClawAdapterResult 及其辅助模型的构造、字段校验和行为。
"""

from __future__ import annotations

import pytest

from src.domain.models.openclaw_adapter_result import (
    OpenClawAdapterResult,
    AdapterExplanation,
    AdapterWarning,
    AdapterError,
)
from src.domain.models.handoff_bundle import HandoffBundle, HandoffFile, Manifest


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_bundle() -> HandoffBundle:
    """示例 HandoffBundle"""
    files = [
        HandoffFile(filename="TASK.md", content="# TASK"),
        HandoffFile(filename="TEAM.md", content="# TEAM"),
    ]
    manifest = Manifest(
        task_id="tsk_001",
        generated_at="2026-03-21T00:00:00Z",
        files=["TASK.md", "TEAM.md"],
    )
    return HandoffBundle(files=files, manifest=manifest)


# =============================================================================
# AdapterExplanation Tests
# =============================================================================

class TestAdapterExplanation:
    """AdapterExplanation 测试"""

    def test_create_explanation(self):
        """测试创建解释"""
        explanation = AdapterExplanation(
            subject="file_generation",
            description="Generated TASK.md from task_spec",
            details={"source": "task_spec.goal"},
        )

        assert explanation.subject == "file_generation"
        assert explanation.description == "Generated TASK.md from task_spec"
        assert explanation.details["source"] == "task_spec.goal"

    def test_explanation_default_details(self):
        """测试默认详情"""
        explanation = AdapterExplanation(
            subject="test",
            description="Test explanation",
        )

        assert explanation.details == {}

    def test_explanation_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            AdapterExplanation(
                subject="test",
                description="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# AdapterWarning Tests
# =============================================================================

class TestAdapterWarning:
    """AdapterWarning 测试"""

    def test_create_warning(self):
        """测试创建警告"""
        warning = AdapterWarning(
            code="EMPTY_SKILL_WHITELIST",
            message="No skills in whitelist, all skills disabled",
            details={},
        )

        assert warning.code == "EMPTY_SKILL_WHITELIST"
        assert warning.message == "No skills in whitelist, all skills disabled"

    def test_warning_default_details(self):
        """测试默认详情"""
        warning = AdapterWarning(
            code="TEST",
            message="Test warning",
        )

        assert warning.details == {}

    def test_warning_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            AdapterWarning(
                code="TEST",
                message="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# AdapterError Tests
# =============================================================================

class TestAdapterError:
    """AdapterError 测试"""

    def test_create_error(self):
        """测试创建错误"""
        error = AdapterError(
            code="MISSING_REQUIRED_FIELD",
            message="Task spec is missing goal",
            details={"field": "goal"},
        )

        assert error.code == "MISSING_REQUIRED_FIELD"
        assert error.message == "Task spec is missing goal"
        assert error.details["field"] == "goal"

    def test_error_default_details(self):
        """测试默认详情"""
        error = AdapterError(
            code="TEST",
            message="Test error",
        )

        assert error.details == {}

    def test_error_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            AdapterError(
                code="TEST",
                message="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# OpenClawAdapterResult Tests
# =============================================================================

class TestOpenClawAdapterResult:
    """OpenClawAdapterResult 测试"""

    def test_create_result_with_bundle(self, sample_bundle: HandoffBundle):
        """测试创建带 bundle 的结果"""
        result = OpenClawAdapterResult(bundle=sample_bundle)

        assert result.bundle == sample_bundle
        assert result.warnings == []
        assert result.errors == []
        assert result.explanations == []
        assert result.is_success is True

    def test_result_with_warnings(self, sample_bundle: HandoffBundle):
        """测试带警告的结果"""
        warnings = [
            AdapterWarning(code="WARN1", message="Warning 1"),
        ]
        result = OpenClawAdapterResult(
            bundle=sample_bundle,
            warnings=warnings,
        )

        assert len(result.warnings) == 1
        assert result.is_success is True

    def test_result_with_errors(self):
        """测试带错误的结果"""
        errors = [
            AdapterError(code="ERR1", message="Error 1"),
        ]
        result = OpenClawAdapterResult(
            bundle=None,
            errors=errors,
        )

        assert result.bundle is None
        assert len(result.errors) == 1
        assert result.is_success is False

    def test_result_with_explanations(self, sample_bundle: HandoffBundle):
        """测试带解释的结果"""
        explanations = [
            AdapterExplanation(
                subject="test",
                description="Test explanation",
            ),
        ]
        result = OpenClawAdapterResult(
            bundle=sample_bundle,
            explanations=explanations,
        )

        assert len(result.explanations) == 1

    def test_result_default_values(self):
        """测试默认值"""
        result = OpenClawAdapterResult(bundle=None)

        assert result.bundle is None
        assert result.warnings == []
        assert result.errors == []
        assert result.explanations == []
        assert result.is_success is False

    def test_result_extra_fields_forbidden(self, sample_bundle: HandoffBundle):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            OpenClawAdapterResult(
                bundle=sample_bundle,
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# IsSuccess Logic Tests
# =============================================================================

class TestOpenClawAdapterResultIsSuccess:
    """is_success 逻辑测试"""

    def test_success_with_bundle(self, sample_bundle: HandoffBundle):
        """测试有 bundle 时为成功"""
        result = OpenClawAdapterResult(bundle=sample_bundle)

        assert result.is_success is True

    def test_failure_without_bundle(self):
        """测试无 bundle 时为失败"""
        result = OpenClawAdapterResult(bundle=None)

        assert result.is_success is False

    def test_failure_with_errors(self, sample_bundle: HandoffBundle):
        """测试有错误时为失败"""
        result = OpenClawAdapterResult(
            bundle=sample_bundle,
            errors=[
                AdapterError(code="ERR", message="Error"),
            ],
        )

        assert result.is_success is False

    def test_success_with_warnings(self, sample_bundle: HandoffBundle):
        """测试有警告但仍成功"""
        result = OpenClawAdapterResult(
            bundle=sample_bundle,
            warnings=[
                AdapterWarning(code="WARN", message="Warning"),
            ],
        )

        assert result.is_success is True


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestOpenClawAdapterResultEdgeCases:
    """边界情况测试"""

    def test_multiple_warnings_and_errors(self):
        """测试多个警告和错误"""
        result = OpenClawAdapterResult(
            bundle=None,
            warnings=[
                AdapterWarning(code="WARN1", message="Warning 1"),
                AdapterWarning(code="WARN2", message="Warning 2"),
            ],
            errors=[
                AdapterError(code="ERR1", message="Error 1"),
                AdapterError(code="ERR2", message="Error 2"),
            ],
        )

        assert len(result.warnings) == 2
        assert len(result.errors) == 2
        assert result.is_success is False

    def test_result_with_detailed_explanations(self, sample_bundle: HandoffBundle):
        """测试带详细解释的结果"""
        explanations = [
            AdapterExplanation(
                subject="task_md_generation",
                description="Generated TASK.md from task_spec",
                details={"goal": "Test goal"},
            ),
            AdapterExplanation(
                subject="skill_whitelist",
                description="Output skill whitelist from skill_pack",
                details={"skills": ["skill_001", "skill_002"]},
            ),
        ]
        result = OpenClawAdapterResult(
            bundle=sample_bundle,
            explanations=explanations,
        )

        assert len(result.explanations) == 2


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestOpenClawAdapterResultSchemaAlignment:
    """Schema 一致性测试"""

    def test_result_has_required_fields(self, sample_bundle: HandoffBundle):
        """测试结果有所有必需字段"""
        result = OpenClawAdapterResult(bundle=sample_bundle)

        assert hasattr(result, "bundle")
        assert hasattr(result, "warnings")
        assert hasattr(result, "errors")
        assert hasattr(result, "explanations")
        assert hasattr(result, "is_success")

    def test_explanation_has_required_fields(self):
        """测试解释有所有必需字段"""
        explanation = AdapterExplanation(subject="test", description="Test")

        assert hasattr(explanation, "subject")
        assert hasattr(explanation, "description")
        assert hasattr(explanation, "details")

    def test_warning_has_required_fields(self):
        """测试警告有所有必需字段"""
        warning = AdapterWarning(code="TEST", message="Test")

        assert hasattr(warning, "code")
        assert hasattr(warning, "message")
        assert hasattr(warning, "details")

    def test_error_has_required_fields(self):
        """测试错误有所有必需字段"""
        error = AdapterError(code="TEST", message="Test")

        assert hasattr(error, "code")
        assert hasattr(error, "message")
        assert hasattr(error, "details")