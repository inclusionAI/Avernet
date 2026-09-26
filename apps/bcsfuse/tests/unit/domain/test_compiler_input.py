"""
Tests for CompilerInput Domain Model

M8: Execution Packet Compiler

测试 CompilerInput 和 CompilerHints 模型的构造、字段校验和行为。
"""

from __future__ import annotations

import pytest

from src.domain.models.compiler_input import CompilerInput, CompilerHints
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.team_spec import TeamSpec, RoleAssignment
from src.domain.models.candidate_bundle import CandidateBundle
from src.domain.models.workspace import Workspace, WorkspaceStatus
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel, Availability, TrustLevel,
)


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
def sample_worker() -> Worker:
    """示例 Worker"""
    return Worker(
        id="wrk_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(name="Test Bot", handle="test_bot"),
        responsibilities=["Development"],
        capabilities=[Capability(name="coding", level=CapabilityLevel.ADVANCED)],
        domains=["development"],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.0,
        ),
    )


@pytest.fixture
def sample_candidate_bundle(sample_worker: Worker) -> CandidateBundle:
    """示例 CandidateBundle"""
    return CandidateBundle(workers=[sample_worker])


@pytest.fixture
def sample_workspace(sample_team_spec: TeamSpec) -> Workspace:
    """示例 Workspace"""
    return Workspace(
        id="wsp_test_001",
        task_id="tsk_test_001",
        team_spec=sample_team_spec,
        status=WorkspaceStatus.ASSEMBLED,
    )


# =============================================================================
# CompilerHints Tests
# =============================================================================

class TestCompilerHints:
    """CompilerHints 测试"""

    def test_create_default_hints(self):
        """测试创建默认 CompilerHints"""
        hints = CompilerHints()

        assert hints.include_full_context is True
        assert hints.generate_memory_summary is True
        assert hints.strict_guardrails is True
        assert hints.max_context_items is None

    def test_create_hints_with_values(self):
        """测试创建带值的 CompilerHints"""
        hints = CompilerHints(
            include_full_context=False,
            generate_memory_summary=False,
            strict_guardrails=False,
            max_context_items=10,
        )

        assert hints.include_full_context is False
        assert hints.generate_memory_summary is False
        assert hints.strict_guardrails is False
        assert hints.max_context_items == 10

    def test_hints_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompilerHints(
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# CompilerInput Tests
# =============================================================================

class TestCompilerInput:
    """CompilerInput 测试"""

    def test_create_input_with_required_fields(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试创建带所有必需字段的 CompilerInput"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        assert input_data.task_spec == sample_task_spec
        assert input_data.plan_draft == sample_plan_draft
        assert input_data.team_spec == sample_team_spec
        assert input_data.candidate_bundle == sample_candidate_bundle
        assert input_data.workspace == sample_workspace

    def test_input_default_hints(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试默认 hints"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        assert input_data.hints is not None
        assert input_data.hints.include_full_context is True

    def test_input_with_custom_hints(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试自定义 hints"""
        hints = CompilerHints(
            include_full_context=False,
            max_context_items=5,
        )
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
            hints=hints,
        )

        assert input_data.hints.include_full_context is False
        assert input_data.hints.max_context_items == 5

    def test_input_required_fields(self):
        """测试必需字段验证"""
        with pytest.raises(Exception):  # ValidationError
            CompilerInput()  # Missing all required fields

    def test_input_extra_fields_forbidden(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompilerInput(
                task_spec=sample_task_spec,
                plan_draft=sample_plan_draft,
                team_spec=sample_team_spec,
                candidate_bundle=sample_candidate_bundle,
                workspace=sample_workspace,
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# Input Consistency Tests
# =============================================================================

class TestCompilerInputConsistency:
    """输入一致性测试"""

    def test_task_plan_alignment(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试 TaskSpec 和 PlanDraft 的 task_id 对齐"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        assert input_data.task_spec.id == input_data.plan_draft.task_id

    def test_workspace_task_alignment(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试 Workspace 和 TaskSpec 的 task_id 对齐"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        assert input_data.workspace.task_id == input_data.task_spec.id


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestCompilerInputEdgeCases:
    """边界情况测试"""

    def test_input_with_empty_candidate_bundle(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_workspace: Workspace,
    ):
        """测试空 CandidateBundle"""
        empty_bundle = CandidateBundle()
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=empty_bundle,
            workspace=sample_workspace,
        )

        assert input_data.candidate_bundle == empty_bundle
        assert len(input_data.candidate_bundle.workers) == 0

    def test_input_with_minimal_workspace(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试最小 Workspace"""
        minimal_workspace = Workspace(
            id="wsp_minimal",
            task_id="tsk_test_001",
            team_spec=sample_team_spec,
            status=WorkspaceStatus.DRAFT,
        )
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=minimal_workspace,
        )

        assert input_data.workspace.status == WorkspaceStatus.DRAFT.value


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestCompilerInputSchemaAlignment:
    """Schema 一致性测试"""

    def test_input_has_required_fields(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试 CompilerInput 有所有必需字段"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        assert hasattr(input_data, "task_spec")
        assert hasattr(input_data, "plan_draft")
        assert hasattr(input_data, "team_spec")
        assert hasattr(input_data, "candidate_bundle")
        assert hasattr(input_data, "workspace")
        assert hasattr(input_data, "hints")

    def test_hints_has_all_fields(self):
        """测试 CompilerHints 有所有字段"""
        hints = CompilerHints()

        assert hasattr(hints, "include_full_context")
        assert hasattr(hints, "generate_memory_summary")
        assert hasattr(hints, "strict_guardrails")
        assert hasattr(hints, "max_context_items")