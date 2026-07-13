"""
Tests for WorkspaceAssemblyInput Domain Model

M7: Workspace / Group Assembly

测试 WorkspaceAssemblyInput 模型的构造、字段校验和行为。
"""

from __future__ import annotations

import pytest

from src.domain.models.workspace_assembly_input import (
    WorkspaceAssemblyInput,
    AssemblyHints,
)
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.team_spec import TeamSpec, RoleAssignment
from src.domain.models.candidate_bundle import CandidateBundle, KnowledgeItem
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel,
    Availability, TrustLevel,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_task_spec() -> TaskSpec:
    """示例 TaskSpec"""
    return TaskSpec(
        id="tsk_architecture_001",
        goal="Design system architecture",
        deliverables=["Architecture document"],
        constraints=["Must use microservices"],
        success_criteria=["Approved by review"],
        required_capabilities=["system_design"],
        required_knowledge=["architecture"],
        required_resources=["res_wiki_001"],
        risk_level=RiskLevel.LOW,
        unknowns=[],
        subtasks=[],
    )


@pytest.fixture
def sample_plan_draft() -> PlanDraft:
    """示例 PlanDraft"""
    return PlanDraft(
        task_id="tsk_architecture_001",
        strategy="Design first",
        steps=[
            PlanStep(id="s1", title="Design", objective="Create design"),
        ],
        role_requirements=["architect"],
        knowledge_requirements=["architecture"],
        resource_requirements=["res_wiki_001"],
        handoff_strategy="sequential",
        escalation_points=[],
    )


@pytest.fixture
def sample_team_spec() -> TeamSpec:
    """示例 TeamSpec"""
    return TeamSpec(
        team_id="team_architecture_001",
        members=["wrk_architect_001"],
        role_assignments=[
            RoleAssignment(
                worker_id="wrk_architect_001",
                role="architect",
                objective="Design architecture",
            ),
        ],
        selected_skills=["web_search"],
        selected_resources=["res_wiki_001"],
        composition_rationale=["Best match"],
        gaps=[],
    )


@pytest.fixture
def sample_candidate_bundle() -> CandidateBundle:
    """示例 CandidateBundle"""
    worker = Worker(
        id="wrk_architect_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name="Architect Bot",
            handle="architect_bot",
        ),
        responsibilities=["Design systems"],
        capabilities=[
            Capability(name="system_design", level=CapabilityLevel.EXPERT),
        ],
        domains=["architecture"],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
        ),
    )

    knowledge = KnowledgeItem(
        id="kno_001",
        kind="guide",
        title="Architecture Guide",
        summary="System architecture best practices",
        freshness="fresh",
        reliability="high",
        tags=["architecture"],
    )

    return CandidateBundle(
        workers=[worker],
        knowledge_items=[knowledge],
    )


# =============================================================================
# AssemblyHints Tests
# =============================================================================

class TestAssemblyHints:
    """AssemblyHints 测试"""

    def test_create_default_hints(self):
        """测试创建默认 hints"""
        hints = AssemblyHints()

        assert hints.include_all_knowledge is True
        assert hints.include_all_resources is True
        assert hints.generate_initial_threads is False
        assert hints.custom_mount_paths == {}

    def test_create_hints_with_values(self):
        """测试创建带值的 hints"""
        hints = AssemblyHints(
            include_all_knowledge=False,
            include_all_resources=True,
            generate_initial_threads=True,
            custom_mount_paths={"res_001": "/data/external"},
        )

        assert hints.include_all_knowledge is False
        assert hints.include_all_resources is True
        assert hints.generate_initial_threads is True
        assert hints.custom_mount_paths == {"res_001": "/data/external"}

    def test_hints_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            AssemblyHints(extra_field="invalid")  # type: ignore


# =============================================================================
# WorkspaceAssemblyInput Tests
# =============================================================================

class TestWorkspaceAssemblyInput:
    """WorkspaceAssemblyInput 测试"""

    def test_create_input_with_required_fields(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试创建输入"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        assert input_data.task_spec == sample_task_spec
        assert input_data.plan_draft == sample_plan_draft
        assert input_data.team_spec == sample_team_spec
        assert input_data.candidate_bundle == sample_candidate_bundle
        assert input_data.hints is not None

    def test_input_with_custom_hints(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试带自定义 hints 的输入"""
        hints = AssemblyHints(
            include_all_knowledge=False,
            generate_initial_threads=True,
        )

        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            hints=hints,
        )

        assert input_data.hints.include_all_knowledge is False
        assert input_data.hints.generate_initial_threads is True

    def test_input_default_hints(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试默认 hints"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        assert input_data.hints.include_all_knowledge is True
        assert input_data.hints.include_all_resources is True

    def test_input_required_fields(self):
        """测试必填字段"""
        with pytest.raises(Exception):  # ValidationError
            WorkspaceAssemblyInput()  # type: ignore

    def test_input_extra_fields_forbidden(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            WorkspaceAssemblyInput(
                task_spec=sample_task_spec,
                plan_draft=sample_plan_draft,
                team_spec=sample_team_spec,
                candidate_bundle=sample_candidate_bundle,
                extra_field="invalid",  # type: ignore
            )

    def test_input_task_plan_alignment(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 task_spec 和 plan_draft 对齐"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        # plan_draft.task_id 应该等于 task_spec.id
        assert input_data.plan_draft.task_id == input_data.task_spec.id

    def test_input_team_spec_task_alignment(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 team_spec 与 task 关联"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        # team_spec 通过 TeamSpec 对象关联，不直接在 Workspace
        assert input_data.team_spec is not None


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestWorkspaceAssemblyInputEdgeCases:
    """边界情况测试"""

    def test_input_with_empty_candidate_bundle(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
    ):
        """测试空候选集"""
        empty_bundle = CandidateBundle()

        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=empty_bundle,
        )

        assert len(input_data.candidate_bundle.workers) == 0
        assert len(input_data.candidate_bundle.knowledge_items) == 0

    def test_input_with_empty_team_spec_members(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 TeamSpec 需要至少一个成员（由 TeamSpec 自身保证）"""
        # TeamSpec 必须有成员，这是 TeamSpec 的约束
        # 这里测试的是输入能正确传递
        team_spec = TeamSpec(
            team_id="team_minimal",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(
                    worker_id="wrk_001",
                    role="solo",
                    objective="Do everything",
                ),
            ],
            composition_rationale=["Minimal team"],
        )

        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        assert len(input_data.team_spec.members) == 1


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestWorkspaceAssemblyInputSchemaAlignment:
    """一致性测试"""

    def test_input_has_required_fields(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试输入有必需字段"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        assert hasattr(input_data, "task_spec")
        assert hasattr(input_data, "plan_draft")
        assert hasattr(input_data, "team_spec")
        assert hasattr(input_data, "candidate_bundle")
        assert hasattr(input_data, "hints")

    def test_hints_has_all_fields(self):
        """测试 hints 有所有字段"""
        hints = AssemblyHints()

        assert hasattr(hints, "include_all_knowledge")
        assert hasattr(hints, "include_all_resources")
        assert hasattr(hints, "generate_initial_threads")
        assert hasattr(hints, "custom_mount_paths")