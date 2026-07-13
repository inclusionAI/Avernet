"""
Tests for CompositionResult Domain Model

M6: Team Composer / Matchmaker

测试 CompositionResult 模型的构造、字段校验和行为。
"""

from __future__ import annotations

import pytest

from src.domain.models.composition_result import (
    CompositionResult,
    CompositionExplanation,
    CompositionWarning,
    CompositionError,
)
from src.domain.models.team_spec import TeamSpec, RoleAssignment


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_team_spec() -> TeamSpec:
    """示例 TeamSpec"""
    return TeamSpec(
        team_id="team_001",
        members=["wrk_architect_001", "wrk_developer_001"],
        role_assignments=[
            RoleAssignment(
                worker_id="wrk_architect_001",
                role="architect",
                objective="Design architecture",
            ),
            RoleAssignment(
                worker_id="wrk_developer_001",
                role="developer",
                objective="Implement features",
            ),
        ],
        selected_skills=["web_search"],
        selected_resources=["res_wiki_001"],
        composition_rationale=["Best team for the task"],
        gaps=["Missing security reviewer"],
    )


@pytest.fixture
def sample_explanations() -> list[CompositionExplanation]:
    """示例解释列表"""
    return [
        CompositionExplanation(
            worker_id="wrk_architect_001",
            role="architect",
            match_score=0.95,
            selection_reason="Expert in system design",
            capability_match={"system_design": "expert"},
        ),
        CompositionExplanation(
            worker_id="wrk_developer_001",
            role="developer",
            match_score=0.85,
            selection_reason="Strong coding skills",
            capability_match={"coding": "advanced"},
        ),
    ]


# =============================================================================
# CompositionExplanation Tests
# =============================================================================

class TestCompositionExplanation:
    """CompositionExplanation 测试"""

    def test_create_explanation(self):
        """测试创建解释"""
        explanation = CompositionExplanation(
            worker_id="wrk_001",
            role="developer",
            match_score=0.9,
            selection_reason="Strong match",
            capability_match={"coding": "expert"},
        )

        assert explanation.worker_id == "wrk_001"
        assert explanation.role == "developer"
        assert explanation.match_score == 0.9
        assert explanation.selection_reason == "Strong match"
        assert explanation.capability_match == {"coding": "expert"}

    def test_explanation_default_values(self):
        """测试解释默认值"""
        explanation = CompositionExplanation(
            worker_id="wrk_001",
            role="developer",
            match_score=0.8,
            selection_reason="Good match",
        )

        assert explanation.capability_match == {}
        assert explanation.exclusion_reason is None

    def test_explanation_with_exclusion_reason(self):
        """测试带排除原因的解释"""
        explanation = CompositionExplanation(
            worker_id="wrk_001",
            role="developer",
            match_score=0.0,
            selection_reason="",
            exclusion_reason="Not available",
        )

        assert explanation.exclusion_reason == "Not available"

    def test_explanation_score_range(self):
        """测试分数范围"""
        # Valid range
        explanation = CompositionExplanation(
            worker_id="wrk_001",
            role="dev",
            match_score=0.5,
            selection_reason="Match",
        )
        assert explanation.match_score == 0.5

        # Invalid: negative
        with pytest.raises(Exception):  # ValidationError
            CompositionExplanation(
                worker_id="wrk_001",
                role="dev",
                match_score=-0.1,
                selection_reason="Invalid",
            )

        # Invalid: > 1
        with pytest.raises(Exception):  # ValidationError
            CompositionExplanation(
                worker_id="wrk_001",
                role="dev",
                match_score=1.1,
                selection_reason="Invalid",
            )

    def test_explanation_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompositionExplanation(
                worker_id="wrk_001",
                role="dev",
                match_score=0.5,
                selection_reason="Match",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# CompositionWarning Tests
# =============================================================================

class TestCompositionWarning:
    """CompositionWarning 测试"""

    def test_create_warning(self):
        """测试创建警告"""
        warning = CompositionWarning(
            code="INCOMPLETE_COVERAGE",
            message="Not all roles covered",
            details={"missing_roles": ["reviewer"]},
        )

        assert warning.code == "INCOMPLETE_COVERAGE"
        assert warning.message == "Not all roles covered"
        assert warning.details == {"missing_roles": ["reviewer"]}

    def test_warning_default_details(self):
        """测试警告默认 details"""
        warning = CompositionWarning(
            code="LOW_CONFIDENCE",
            message="Low match confidence",
        )

        assert warning.details == {}

    def test_warning_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompositionWarning(
                code="TEST",
                message="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# CompositionError Tests
# =============================================================================

class TestCompositionError:
    """CompositionError 测试"""

    def test_create_error(self):
        """测试创建错误"""
        error = CompositionError(
            code="NO_CANDIDATES",
            message="No candidates available",
            details={"required_roles": ["architect"]},
        )

        assert error.code == "NO_CANDIDATES"
        assert error.message == "No candidates available"
        assert error.details == {"required_roles": ["architect"]}

    def test_error_default_details(self):
        """测试错误默认 details"""
        error = CompositionError(
            code="CONSTRAINT_VIOLATION",
            message="Cannot satisfy constraints",
        )

        assert error.details == {}

    def test_error_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompositionError(
                code="TEST",
                message="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# CompositionResult Tests
# =============================================================================

class TestCompositionResult:
    """CompositionResult 测试"""

    def test_create_result_with_team_spec(
        self,
        sample_team_spec: TeamSpec,
        sample_explanations: list[CompositionExplanation],
    ):
        """测试创建成功的结果"""
        result = CompositionResult(
            team_spec=sample_team_spec,
            explanations=sample_explanations,
        )

        assert result.team_spec == sample_team_spec
        assert len(result.explanations) == 2
        assert result.warnings == []
        assert result.errors == []
        assert result.is_success is True

    def test_result_with_warnings(
        self,
        sample_team_spec: TeamSpec,
        sample_explanations: list[CompositionExplanation],
    ):
        """测试带警告的结果"""
        warnings = [
            CompositionWarning(
                code="INCOMPLETE_COVERAGE",
                message="Not all roles covered",
            )
        ]

        result = CompositionResult(
            team_spec=sample_team_spec,
            explanations=sample_explanations,
            warnings=warnings,
        )

        assert len(result.warnings) == 1
        assert result.is_success is True  # Still success with warnings

    def test_result_with_errors(
        self,
        sample_explanations: list[CompositionExplanation],
    ):
        """测试带错误的结果"""
        errors = [
            CompositionError(
                code="NO_CANDIDATES",
                message="No candidates found",
            )
        ]

        result = CompositionResult(
            team_spec=None,
            explanations=sample_explanations,
            errors=errors,
        )

        assert result.team_spec is None
        assert len(result.errors) == 1
        assert result.is_success is False

    def test_result_default_values(self):
        """测试结果默认值"""
        result = CompositionResult(
            team_spec=None,
            explanations=[],
        )

        assert result.team_spec is None
        assert result.explanations == []
        assert result.warnings == []
        assert result.errors == []
        assert result.is_success is False

    def test_result_extra_fields_forbidden(
        self,
        sample_team_spec: TeamSpec,
        sample_explanations: list[CompositionExplanation],
    ):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompositionResult(
                team_spec=sample_team_spec,
                explanations=sample_explanations,
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# IsSuccess Logic Tests
# =============================================================================

class TestCompositionResultIsSuccess:
    """CompositionResult is_success 逻辑测试"""

    def test_success_with_team_spec(
        self,
        sample_team_spec: TeamSpec,
        sample_explanations: list[CompositionExplanation],
    ):
        """测试有 TeamSpec 时为成功"""
        result = CompositionResult(
            team_spec=sample_team_spec,
            explanations=sample_explanations,
        )

        assert result.is_success is True

    def test_failure_without_team_spec(self):
        """测试无 TeamSpec 时为失败"""
        result = CompositionResult(
            team_spec=None,
            explanations=[],
        )

        assert result.is_success is False

    def test_failure_with_errors(
        self,
        sample_team_spec: TeamSpec,
        sample_explanations: list[CompositionExplanation],
    ):
        """测试有错误时为失败"""
        result = CompositionResult(
            team_spec=sample_team_spec,
            explanations=sample_explanations,
            errors=[
                CompositionError(code="ERR", message="Error")
            ],
        )

        # 有错误应该为失败
        assert result.is_success is False

    def test_success_with_warnings(
        self,
        sample_team_spec: TeamSpec,
        sample_explanations: list[CompositionExplanation],
    ):
        """测试有警告但仍成功"""
        result = CompositionResult(
            team_spec=sample_team_spec,
            explanations=sample_explanations,
            warnings=[
                CompositionWarning(code="WARN", message="Warning")
            ],
        )

        # 警告不影响成功状态
        assert result.is_success is True


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestCompositionResultEdgeCases:
    """CompositionResult 边界情况测试"""

    def test_empty_role_assignments(
        self,
        sample_explanations: list[CompositionExplanation],
    ):
        """测试空角色分配"""
        team_spec = TeamSpec(
            team_id="team_empty",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(
                    worker_id="wrk_001",
                    role="solo",
                    objective="Do everything",
                ),
            ],
            composition_rationale=["Single worker team"],
        )

        result = CompositionResult(
            team_spec=team_spec,
            explanations=sample_explanations[:1],
        )

        assert result.is_success is True
        assert len(result.team_spec.role_assignments) == 1

    def test_large_team_spec(
        self,
        sample_explanations: list[CompositionExplanation],
    ):
        """测试大型团队"""
        members = [f"wrk_{i:03d}" for i in range(50)]
        role_assignments = [
            RoleAssignment(
                worker_id=f"wrk_{i:03d}",
                role="developer",
                objective="Develop",
            )
            for i in range(50)
        ]

        team_spec = TeamSpec(
            team_id="team_large",
            members=members,
            role_assignments=role_assignments,
            composition_rationale=["Large team"],
        )

        result = CompositionResult(
            team_spec=team_spec,
            explanations=sample_explanations[:1],
        )

        assert len(result.team_spec.members) == 50
        assert len(result.team_spec.role_assignments) == 50

    def test_multiple_warnings_and_errors(self):
        """测试多个警告和错误"""
        result = CompositionResult(
            team_spec=None,
            explanations=[],
            warnings=[
                CompositionWarning(code="WARN1", message="Warning 1"),
                CompositionWarning(code="WARN2", message="Warning 2"),
            ],
            errors=[
                CompositionError(code="ERR1", message="Error 1"),
                CompositionError(code="ERR2", message="Error 2"),
            ],
        )

        assert len(result.warnings) == 2
        assert len(result.errors) == 2
        assert result.is_success is False


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestCompositionResultSchemaAlignment:
    """CompositionResult 与 schema 一致性测试"""

    def test_result_has_required_fields(
        self,
        sample_team_spec: TeamSpec,
        sample_explanations: list[CompositionExplanation],
    ):
        """测试结果有必需字段"""
        result = CompositionResult(
            team_spec=sample_team_spec,
            explanations=sample_explanations,
        )

        assert hasattr(result, "team_spec")
        assert hasattr(result, "warnings")
        assert hasattr(result, "errors")
        assert hasattr(result, "explanations")
        assert hasattr(result, "is_success")

    def test_explanation_has_required_fields(self):
        """测试解释有必需字段"""
        explanation = CompositionExplanation(
            worker_id="wrk_001",
            role="developer",
            match_score=0.9,
            selection_reason="Selected for skills",
        )

        assert hasattr(explanation, "worker_id")
        assert hasattr(explanation, "role")
        assert hasattr(explanation, "match_score")
        assert hasattr(explanation, "selection_reason")
        assert hasattr(explanation, "capability_match")
        assert hasattr(explanation, "exclusion_reason")