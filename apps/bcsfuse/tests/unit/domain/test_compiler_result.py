"""
Tests for CompilerResult Domain Model

M8: Execution Packet Compiler

测试 CompilerResult 及其辅助模型的构造、字段校验和行为。
"""

from __future__ import annotations

import pytest

from src.domain.models.compiler_result import (
    CompilerResult,
    CompilerExplanation,
    CompilerWarning,
    CompilerError,
)
from src.domain.models.execution_packet import (
    ExecutionPacket,
    ContextPack,
    ResourcePack,
    SkillPack,
    Guardrails,
    OutputContract,
)
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.team_spec import TeamSpec, RoleAssignment


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_task_spec() -> TaskSpec:
    """示例 TaskSpec"""
    return TaskSpec(
        id="tsk_test_001",
        goal="Test goal",
        deliverables=["Test deliverable"],
        constraints=[],
        success_criteria=["Success"],
        required_capabilities=["coding"],
        required_knowledge=[],
        required_resources=[],
        risk_level=RiskLevel.LOW,
        unknowns=[],
        subtasks=[],
    )


@pytest.fixture
def sample_plan_draft() -> PlanDraft:
    """示例 PlanDraft"""
    return PlanDraft(
        task_id="tsk_test_001",
        strategy="Test strategy",
        steps=[PlanStep(id="s1", title="Step 1", objective="Test")],
        role_requirements=["developer"],
        knowledge_requirements=[],
        resource_requirements=[],
        handoff_strategy="sequential",
        escalation_points=[],
    )


@pytest.fixture
def sample_team_spec() -> TeamSpec:
    """示例 TeamSpec"""
    return TeamSpec(
        team_id="team_test_001",
        members=["wrk_001"],
        role_assignments=[
            RoleAssignment(
                worker_id="wrk_001",
                role="developer",
                objective="Develop",
            ),
        ],
        composition_rationale=["Test team"],
    )


@pytest.fixture
def sample_packet(
    sample_task_spec: TaskSpec,
    sample_plan_draft: PlanDraft,
    sample_team_spec: TeamSpec,
) -> ExecutionPacket:
    """示例 ExecutionPacket"""
    return ExecutionPacket(
        task_spec=sample_task_spec,
        plan_draft=sample_plan_draft,
        team_spec=sample_team_spec,
        context_pack=ContextPack(summary="Test context"),
        resource_pack=ResourcePack(),
        skill_pack=SkillPack(sandbox_required=False),
        guardrails=Guardrails(),
        output_contract=OutputContract(must_include_validation=True),
        launch_prompt="Test prompt",
    )


# =============================================================================
# CompilerExplanation Tests
# =============================================================================

class TestCompilerExplanation:
    """CompilerExplanation 测试"""

    def test_create_explanation(self):
        """测试创建解释"""
        explanation = CompilerExplanation(
            subject="context_compilation",
            description="Compiled context from knowledge items",
            details={"item_count": 5},
        )

        assert explanation.subject == "context_compilation"
        assert explanation.description == "Compiled context from knowledge items"
        assert explanation.details["item_count"] == 5

    def test_explanation_default_details(self):
        """测试默认详情"""
        explanation = CompilerExplanation(
            subject="test",
            description="Test explanation",
        )

        assert explanation.details == {}

    def test_explanation_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompilerExplanation(
                subject="test",
                description="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# CompilerWarning Tests
# =============================================================================

class TestCompilerWarning:
    """CompilerWarning 测试"""

    def test_create_warning(self):
        """测试创建警告"""
        warning = CompilerWarning(
            code="INCOMPLETE_CONTEXT",
            message="Some context items were trimmed",
            details={"trimmed_count": 3},
        )

        assert warning.code == "INCOMPLETE_CONTEXT"
        assert warning.message == "Some context items were trimmed"
        assert warning.details["trimmed_count"] == 3

    def test_warning_default_details(self):
        """测试默认详情"""
        warning = CompilerWarning(
            code="TEST",
            message="Test warning",
        )

        assert warning.details == {}

    def test_warning_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompilerWarning(
                code="TEST",
                message="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# CompilerError Tests
# =============================================================================

class TestCompilerError:
    """CompilerError 测试"""

    def test_create_error(self):
        """测试创建错误"""
        error = CompilerError(
            code="MISSING_REQUIRED_RESOURCE",
            message="Required resource not found in bundle",
            details={"resource_id": "res_001"},
        )

        assert error.code == "MISSING_REQUIRED_RESOURCE"
        assert error.message == "Required resource not found in bundle"
        assert error.details["resource_id"] == "res_001"

    def test_error_default_details(self):
        """测试默认详情"""
        error = CompilerError(
            code="TEST",
            message="Test error",
        )

        assert error.details == {}

    def test_error_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompilerError(
                code="TEST",
                message="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# CompilerResult Tests
# =============================================================================

class TestCompilerResult:
    """CompilerResult 测试"""

    def test_create_result_with_packet(self, sample_packet: ExecutionPacket):
        """测试创建带 packet 的结果"""
        result = CompilerResult(packet=sample_packet)

        assert result.packet == sample_packet
        assert result.warnings == []
        assert result.errors == []
        assert result.explanations == []
        assert result.is_success is True

    def test_result_with_warnings(self, sample_packet: ExecutionPacket):
        """测试带警告的结果"""
        warnings = [
            CompilerWarning(code="WARN1", message="Warning 1"),
        ]
        result = CompilerResult(
            packet=sample_packet,
            warnings=warnings,
        )

        assert len(result.warnings) == 1
        assert result.is_success is True

    def test_result_with_errors(self):
        """测试带错误的结果"""
        errors = [
            CompilerError(code="ERR1", message="Error 1"),
        ]
        result = CompilerResult(
            packet=None,
            errors=errors,
        )

        assert result.packet is None
        assert len(result.errors) == 1
        assert result.is_success is False

    def test_result_with_explanations(self, sample_packet: ExecutionPacket):
        """测试带解释的结果"""
        explanations = [
            CompilerExplanation(
                subject="test",
                description="Test explanation",
            ),
        ]
        result = CompilerResult(
            packet=sample_packet,
            explanations=explanations,
        )

        assert len(result.explanations) == 1

    def test_result_default_values(self):
        """测试默认值"""
        result = CompilerResult(packet=None)

        assert result.packet is None
        assert result.warnings == []
        assert result.errors == []
        assert result.explanations == []
        assert result.is_success is False

    def test_result_extra_fields_forbidden(self, sample_packet: ExecutionPacket):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompilerResult(
                packet=sample_packet,
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# IsSuccess Logic Tests
# =============================================================================

class TestCompilerResultIsSuccess:
    """is_success 逻辑测试"""

    def test_success_with_packet(self, sample_packet: ExecutionPacket):
        """测试有 packet 时为成功"""
        result = CompilerResult(packet=sample_packet)

        assert result.is_success is True

    def test_failure_without_packet(self):
        """测试无 packet 时为失败"""
        result = CompilerResult(packet=None)

        assert result.is_success is False

    def test_failure_with_errors(self, sample_packet: ExecutionPacket):
        """测试有错误时为失败"""
        result = CompilerResult(
            packet=sample_packet,
            errors=[
                CompilerError(code="ERR", message="Error"),
            ],
        )

        assert result.is_success is False

    def test_success_with_warnings(self, sample_packet: ExecutionPacket):
        """测试有警告但仍成功"""
        result = CompilerResult(
            packet=sample_packet,
            warnings=[
                CompilerWarning(code="WARN", message="Warning"),
            ],
        )

        assert result.is_success is True


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestCompilerResultEdgeCases:
    """边界情况测试"""

    def test_multiple_warnings_and_errors(self):
        """测试多个警告和错误"""
        result = CompilerResult(
            packet=None,
            warnings=[
                CompilerWarning(code="WARN1", message="Warning 1"),
                CompilerWarning(code="WARN2", message="Warning 2"),
            ],
            errors=[
                CompilerError(code="ERR1", message="Error 1"),
                CompilerError(code="ERR2", message="Error 2"),
            ],
        )

        assert len(result.warnings) == 2
        assert len(result.errors) == 2
        assert result.is_success is False

    def test_result_with_detailed_explanations(self, sample_packet: ExecutionPacket):
        """测试带详细解释的结果"""
        explanations = [
            CompilerExplanation(
                subject="context_compilation",
                description="Context was compiled from 5 knowledge items",
                details={"items": ["kno_001", "kno_002"], "trimmed": 0},
            ),
            CompilerExplanation(
                subject="guardrails_generation",
                description="Generated 3 rules from task constraints",
                details={"rules": ["No writes", "No external calls"]},
            ),
        ]
        result = CompilerResult(
            packet=sample_packet,
            explanations=explanations,
        )

        assert len(result.explanations) == 2


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestCompilerResultSchemaAlignment:
    """Schema 一致性测试"""

    def test_result_has_required_fields(self, sample_packet: ExecutionPacket):
        """测试结果有所有必需字段"""
        result = CompilerResult(packet=sample_packet)

        assert hasattr(result, "packet")
        assert hasattr(result, "warnings")
        assert hasattr(result, "errors")
        assert hasattr(result, "explanations")
        assert hasattr(result, "is_success")

    def test_explanation_has_required_fields(self):
        """测试解释有所有必需字段"""
        explanation = CompilerExplanation(subject="test", description="Test")

        assert hasattr(explanation, "subject")
        assert hasattr(explanation, "description")
        assert hasattr(explanation, "details")

    def test_warning_has_required_fields(self):
        """测试警告有所有必需字段"""
        warning = CompilerWarning(code="TEST", message="Test")

        assert hasattr(warning, "code")
        assert hasattr(warning, "message")
        assert hasattr(warning, "details")

    def test_error_has_required_fields(self):
        """测试错误有所有必需字段"""
        error = CompilerError(code="TEST", message="Test")

        assert hasattr(error, "code")
        assert hasattr(error, "message")
        assert hasattr(error, "details")