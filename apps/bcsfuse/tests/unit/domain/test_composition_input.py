"""
Tests for CompositionInput Domain Model

M6: Team Composer / Matchmaker

测试 CompositionInput 模型的构造、字段校验和行为。
"""

from __future__ import annotations

import pytest

from src.domain.models.composition_input import CompositionInput, CompositionConstraints
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.candidate_bundle import CandidateBundle, KnowledgeItem
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel, SkillRef, ResourceRef,
    SkillSource, TrustLevel, ResourceKind, ResourceAccess, Availability,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_task_spec() -> TaskSpec:
    """示例 TaskSpec"""
    return TaskSpec(
        id="tsk_test_001",
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
        task_id="tsk_test_001",
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
def sample_candidate_bundle() -> CandidateBundle:
    """示例 CandidateBundle"""
    worker = Worker(
        id="wrk_architect_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name="Architect Bot",
            handle="architect_bot",
            title="System Architect",
        ),
        responsibilities=["Design systems", "Create documentation"],
        capabilities=[
            Capability(name="system_design", level=CapabilityLevel.EXPERT),
            Capability(name="documentation", level=CapabilityLevel.ADVANCED),
        ],
        domains=["architecture"],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.0,
        ),
    )

    knowledge = KnowledgeItem(
        id="kno_001",
        kind="guide",
        title="Architecture Guide",
        summary="System architecture best practices",
        freshness="fresh",
        reliability="high",
        tags=["architecture", "design"],
    )

    skill = SkillRef(
        name="web_search",
        source=SkillSource.BUILTIN,
        trust_level=TrustLevel.TRUSTED,
    )

    resource = ResourceRef(
        id="res_wiki_001",
        kind=ResourceKind.FILE,
        name="Architecture Wiki",
        access=ResourceAccess.READ,
    )

    return CandidateBundle(
        workers=[worker],
        knowledge_items=[knowledge],
        skills=[skill],
        resources=[resource],
        evidence=[],
    )


# =============================================================================
# CompositionConstraints Tests
# =============================================================================

class TestCompositionConstraints:
    """CompositionConstraints 测试"""

    def test_create_default_constraints(self):
        """测试创建默认约束"""
        constraints = CompositionConstraints()

        assert constraints.max_team_size is None
        assert constraints.min_team_size == 1
        assert constraints.require_all_roles is True
        assert constraints.balance_workload is True
        assert constraints.prefer_diverse_capabilities is False

    def test_create_constraints_with_values(self):
        """测试创建带值的约束"""
        constraints = CompositionConstraints(
            max_team_size=5,
            min_team_size=2,
            require_all_roles=False,
            balance_workload=False,
            prefer_diverse_capabilities=True,
        )

        assert constraints.max_team_size == 5
        assert constraints.min_team_size == 2
        assert constraints.require_all_roles is False
        assert constraints.balance_workload is False
        assert constraints.prefer_diverse_capabilities is True

    def test_constraints_validation_min_exceeds_max(self):
        """测试 min > max 时应该失败"""
        with pytest.raises(Exception):  # ValidationError
            CompositionConstraints(min_team_size=10, max_team_size=5)

    def test_constraints_negative_team_size(self):
        """测试负数团队大小"""
        with pytest.raises(Exception):  # ValidationError
            CompositionConstraints(min_team_size=-1)

    def test_constraints_zero_min_team_size(self):
        """测试零最小团队大小"""
        # 应该允许 min_team_size=1（至少一个成员）
        constraints = CompositionConstraints(min_team_size=1)
        assert constraints.min_team_size == 1

    def test_constraints_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompositionConstraints(extra_field="invalid")  # type: ignore


# =============================================================================
# CompositionInput Tests
# =============================================================================

class TestCompositionInput:
    """CompositionInput 测试"""

    def test_create_composition_input_with_required_fields(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试使用必填字段创建 CompositionInput"""
        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
        )

        assert input_data.task_spec == sample_task_spec
        assert input_data.plan_draft == sample_plan_draft
        assert input_data.candidate_bundle == sample_candidate_bundle
        assert input_data.constraints is not None

    def test_composition_input_with_custom_constraints(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试带自定义约束的 CompositionInput"""
        constraints = CompositionConstraints(
            max_team_size=3,
            require_all_roles=False,
        )

        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
            constraints=constraints,
        )

        assert input_data.constraints.max_team_size == 3
        assert input_data.constraints.require_all_roles is False

    def test_composition_input_default_constraints(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 CompositionInput 默认约束"""
        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
        )

        # 应该有默认约束
        assert input_data.constraints is not None
        assert input_data.constraints.min_team_size == 1
        assert input_data.constraints.require_all_roles is True

    def test_composition_input_required_fields(
        self,
        sample_candidate_bundle: CandidateBundle,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
    ):
        """测试 CompositionInput 必填字段"""
        # 缺少 task_spec
        with pytest.raises(Exception):  # ValidationError
            CompositionInput(  # type: ignore
                plan_draft=sample_plan_draft,
                candidate_bundle=sample_candidate_bundle,
            )

    def test_composition_input_extra_fields_forbidden(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 CompositionInput 禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            CompositionInput(
                task_spec=sample_task_spec,
                plan_draft=sample_plan_draft,
                candidate_bundle=sample_candidate_bundle,
                extra_field="invalid",  # type: ignore
            )

    def test_composition_input_task_plan_alignment(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 task_spec 和 plan_draft 的对齐"""
        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
        )

        # plan_draft.task_id 应该等于 task_spec.id
        assert input_data.plan_draft.task_id == input_data.task_spec.id


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestCompositionInputEdgeCases:
    """CompositionInput 边界情况测试"""

    def test_empty_candidate_bundle(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
    ):
        """测试空候选集"""
        empty_bundle = CandidateBundle()

        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=empty_bundle,
        )

        assert len(input_data.candidate_bundle.workers) == 0
        assert len(input_data.candidate_bundle.knowledge_items) == 0

    def test_large_candidate_bundle(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
    ):
        """测试大型候选集"""
        # 创建大量 worker
        workers = [
            Worker(
                id=f"wrk_{i:03d}",
                type=WorkerType.BOT,
                identity=WorkerIdentity(
                    name=f"Worker {i}",
                    handle=f"worker_{i}",
                ),
                responsibilities=["Development"],
                capabilities=[
                    Capability(name="coding", level=CapabilityLevel.ADVANCED),
                ],
                domains=["development"],
                state=WorkerState(
                    availability=Availability.AVAILABLE,
                    trust_level=TrustLevel.TRUSTED,
                    current_load=0.0,
                ),
            )
            for i in range(100)
        ]

        large_bundle = CandidateBundle(workers=workers)

        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=large_bundle,
        )

        assert len(input_data.candidate_bundle.workers) == 100

    def test_single_worker_bundle(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试单 worker 候选集"""
        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
        )

        assert len(input_data.candidate_bundle.workers) == 1

    def test_constraints_boundary_values(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试约束边界值"""
        # 最小团队大小 = 1
        constraints = CompositionConstraints(min_team_size=1, max_team_size=1)

        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
            constraints=constraints,
        )

        assert input_data.constraints.min_team_size == 1
        assert input_data.constraints.max_team_size == 1


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestCompositionInputSchemaAlignment:
    """CompositionInput 与 schema 一致性测试"""

    def test_composition_input_has_required_fields(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 CompositionInput 有必需字段"""
        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
        )

        assert hasattr(input_data, "task_spec")
        assert hasattr(input_data, "plan_draft")
        assert hasattr(input_data, "candidate_bundle")
        assert hasattr(input_data, "constraints")

    def test_constraints_has_all_fields(self):
        """测试 Constraints 有所有字段"""
        constraints = CompositionConstraints()

        assert hasattr(constraints, "max_team_size")
        assert hasattr(constraints, "min_team_size")
        assert hasattr(constraints, "require_all_roles")
        assert hasattr(constraints, "balance_workload")
        assert hasattr(constraints, "prefer_diverse_capabilities")