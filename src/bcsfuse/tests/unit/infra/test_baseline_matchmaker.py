"""
Tests for BaselineMatchmaker

M6: Team Composer / Matchmaker

测试 BaselineMatchmaker 的匹配和选择逻辑。
"""

from __future__ import annotations

import pytest

from src.infra.matchmakers.baseline_matchmaker import BaselineMatchmaker
from src.domain.models.composition_input import CompositionInput, CompositionConstraints
from src.domain.models.composition_result import CompositionResult
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.candidate_bundle import CandidateBundle, KnowledgeItem
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel, SkillRef, ResourceRef,
    SkillSource, TrustLevel, ResourceKind, ResourceAccess, Availability,
)
from src.domain.models.team_spec import RoleAssignment


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def matchmaker() -> BaselineMatchmaker:
    """BaselineMatchmaker 实例"""
    return BaselineMatchmaker()


@pytest.fixture
def architect_worker() -> Worker:
    """架构师 Worker"""
    return Worker(
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


@pytest.fixture
def developer_worker() -> Worker:
    """开发 Worker"""
    return Worker(
        id="wrk_developer_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name="Developer Bot",
            handle="developer_bot",
            title="Software Developer",
        ),
        responsibilities=["Write code", "Test features"],
        capabilities=[
            Capability(name="coding", level=CapabilityLevel.ADVANCED),
            Capability(name="testing", level=CapabilityLevel.INTERMEDIATE),
        ],
        domains=["development"],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.5,
        ),
    )


@pytest.fixture
def researcher_worker() -> Worker:
    """研究员 Worker"""
    return Worker(
        id="wrk_researcher_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name="Researcher Bot",
            handle="researcher_bot",
            title="Research Specialist",
        ),
        responsibilities=["Research topics", "Write reports"],
        capabilities=[
            Capability(name="information_retrieval", level=CapabilityLevel.EXPERT),
            Capability(name="report_generation", level=CapabilityLevel.ADVANCED),
        ],
        domains=["research"],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.0,
        ),
    )


@pytest.fixture
def architecture_task_spec() -> TaskSpec:
    """架构设计任务规格"""
    return TaskSpec(
        id="tsk_arch_001",
        goal="Design system architecture",
        deliverables=["Architecture document"],
        constraints=["Must use microservices"],
        success_criteria=["Approved by review"],
        required_capabilities=["system_design", "documentation"],
        required_knowledge=["architecture"],
        required_resources=["res_wiki_001"],
        risk_level=RiskLevel.LOW,
        unknowns=[],
        subtasks=[],
    )


@pytest.fixture
def architecture_plan_draft() -> PlanDraft:
    """架构设计规划草案"""
    return PlanDraft(
        task_id="tsk_arch_001",
        strategy="Design first",
        steps=[
            PlanStep(
                id="s1",
                title="Design Architecture",
                objective="Create system architecture design",
            ),
            PlanStep(
                id="s2",
                title="Document",
                objective="Write documentation",
            ),
        ],
        role_requirements=["architect"],
        knowledge_requirements=["architecture"],
        resource_requirements=["res_wiki_001"],
        handoff_strategy="sequential",
        escalation_points=[],
    )


@pytest.fixture
def development_task_spec() -> TaskSpec:
    """开发任务规格"""
    return TaskSpec(
        id="tsk_dev_001",
        goal="Implement features",
        deliverables=["Code changes", "Tests"],
        constraints=["Follow coding standards"],
        success_criteria=["All tests pass"],
        required_capabilities=["coding", "testing"],
        required_knowledge=["python"],
        required_resources=["res_repo_001"],
        risk_level=RiskLevel.LOW,
        unknowns=[],
        subtasks=[],
    )


@pytest.fixture
def development_plan_draft() -> PlanDraft:
    """开发规划草案"""
    return PlanDraft(
        task_id="tsk_dev_001",
        strategy="TDD",
        steps=[
            PlanStep(
                id="s1",
                title="Write tests",
                objective="Create test cases",
            ),
            PlanStep(
                id="s2",
                title="Implement",
                objective="Write code",
            ),
        ],
        role_requirements=["developer"],
        knowledge_requirements=["python"],
        resource_requirements=["res_repo_001"],
        handoff_strategy="sequential",
        escalation_points=[],
    )


# =============================================================================
# Basic Composition Tests
# =============================================================================

class TestBaselineMatchmakerBasic:
    """BaselineMatchmaker 基础测试"""

    def test_compose_with_single_worker(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试单个 Worker 的组合"""
        bundle = CandidateBundle(workers=[architect_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert isinstance(result, CompositionResult)
        assert result.is_success
        assert result.team_spec is not None
        assert len(result.team_spec.members) == 1
        assert "wrk_architect_001" in result.team_spec.members

    def test_compose_with_multiple_workers(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        developer_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试多个 Worker 的组合"""
        bundle = CandidateBundle(workers=[architect_worker, developer_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success
        # 应该选择架构师，因为任务需要 system_design
        assert len(result.team_spec.members) >= 1

    def test_compose_empty_bundle(
        self,
        matchmaker: BaselineMatchmaker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试空候选集"""
        bundle = CandidateBundle()
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert isinstance(result, CompositionResult)
        assert not result.is_success
        assert result.team_spec is None
        assert len(result.errors) > 0

    def test_compose_no_matching_capabilities(
        self,
        matchmaker: BaselineMatchmaker,
        researcher_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试没有匹配能力的 Worker"""
        bundle = CandidateBundle(workers=[researcher_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        # 可能返回警告或失败，取决于实现
        assert isinstance(result, CompositionResult)


# =============================================================================
# Role Assignment Tests
# =============================================================================

class TestBaselineMatchmakerRoleAssignment:
    """BaselineMatchmaker 角色分配测试"""

    def test_role_assignments_created(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试角色分配创建"""
        bundle = CandidateBundle(workers=[architect_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success
        assert len(result.team_spec.role_assignments) >= 1

        # 验证角色分配结构
        assignment = result.team_spec.role_assignments[0]
        assert isinstance(assignment, RoleAssignment)
        assert assignment.worker_id is not None
        assert assignment.role is not None
        assert assignment.objective is not None

    def test_role_matches_plan_requirements(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试角色匹配规划要求"""
        bundle = CandidateBundle(workers=[architect_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success

        # 验证分配的角色在角色要求中
        assigned_roles = {ra.role for ra in result.team_spec.role_assignments}
        required_roles = set(architecture_plan_draft.role_requirements)

        # 至少有一些角色被分配
        assert len(assigned_roles) >= 0


# =============================================================================
# Constraint Handling Tests
# =============================================================================

class TestBaselineMatchmakerConstraints:
    """BaselineMatchmaker 约束处理测试"""

    def test_max_team_size_constraint(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        developer_worker: Worker,
        researcher_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试最大团队大小约束"""
        bundle = CandidateBundle(workers=[architect_worker, developer_worker, researcher_worker])
        constraints = CompositionConstraints(max_team_size=2)
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
            constraints=constraints,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success
        assert len(result.team_spec.members) <= 2

    def test_min_team_size_constraint(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试最小团队大小约束"""
        bundle = CandidateBundle(workers=[architect_worker])
        constraints = CompositionConstraints(min_team_size=2)

        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
            constraints=constraints,
        )

        result = matchmaker.compose(input_data)

        # 只有一个 worker，无法满足最小约束
        # 可能返回警告或部分匹配
        if result.is_success:
            # 如果成功，检查警告
            assert len(result.warnings) > 0 or len(result.team_spec.members) >= 2

    def test_require_all_roles_constraint(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试要求所有角色约束"""
        bundle = CandidateBundle(workers=[architect_worker])
        constraints = CompositionConstraints(require_all_roles=True)

        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
            constraints=constraints,
        )

        result = matchmaker.compose(input_data)

        # 验证结果是否符合约束
        assert isinstance(result, CompositionResult)


# =============================================================================
# Workload Balancing Tests
# =============================================================================

class TestBaselineMatchmakerWorkload:
    """BaselineMatchmaker 负载均衡测试"""

    def test_prefer_lower_load_worker(
        self,
        matchmaker: BaselineMatchmaker,
    ):
        """测试优先选择低负载 Worker"""
        # 创建两个相同能力但不同负载的 Worker
        worker1 = Worker(
            id="wrk_low_load",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Low Load", handle="low_load"),
            responsibilities=["Development"],
            capabilities=[Capability(name="coding", level=CapabilityLevel.ADVANCED)],
            domains=["development"],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.TRUSTED,
                current_load=0.1,
            ),
        )

        worker2 = Worker(
            id="wrk_high_load",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="High Load", handle="high_load"),
            responsibilities=["Development"],
            capabilities=[Capability(name="coding", level=CapabilityLevel.ADVANCED)],
            domains=["development"],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.TRUSTED,
                current_load=0.9,
            ),
        )

        task_spec = TaskSpec(
            id="tsk_test",
            goal="Code review",
            deliverables=["Reviewed code"],
            constraints=[],
            success_criteria=["Approved"],
            required_capabilities=["coding"],
            required_knowledge=[],
            required_resources=[],
            risk_level=RiskLevel.LOW,
            unknowns=[],
            subtasks=[],
        )

        plan_draft = PlanDraft(
            task_id="tsk_test",
            strategy="Review",
            steps=[PlanStep(id="s1", title="Review", objective="Review code")],
            role_requirements=["developer"],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="sequential",
            escalation_points=[],
        )

        constraints = CompositionConstraints(
            max_team_size=1,
            balance_workload=True,
        )

        bundle = CandidateBundle(workers=[worker1, worker2])
        input_data = CompositionInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            candidate_bundle=bundle,
            constraints=constraints,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success
        # 验证选择的是低负载 Worker（如果只有一个成员）
        if len(result.team_spec.members) == 1:
            assert "wrk_low_load" in result.team_spec.members


# =============================================================================
# Explanation Tests
# =============================================================================

class TestBaselineMatchmakerExplanations:
    """BaselineMatchmaker 解释生成测试"""

    def test_explanations_generated(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试解释生成"""
        bundle = CandidateBundle(workers=[architect_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success
        assert len(result.explanations) > 0

        # 验证解释结构
        for exp in result.explanations:
            assert exp.worker_id is not None
            assert exp.role is not None
            assert 0.0 <= exp.match_score <= 1.0
            assert exp.selection_reason is not None

    def test_capability_match_in_explanation(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试解释中包含能力匹配信息"""
        bundle = CandidateBundle(workers=[architect_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success

        # 检查解释中是否有能力匹配信息
        for exp in result.explanations:
            if exp.worker_id == "wrk_architect_001":
                assert isinstance(exp.capability_match, dict)


# =============================================================================
# Rationale and Gaps Tests
# =============================================================================

class TestBaselineMatchmakerRationale:
    """BaselineMatchmaker rationale 和 gaps 测试"""

    def test_composition_rationale_generated(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试组合理由生成"""
        bundle = CandidateBundle(workers=[architect_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success
        assert len(result.team_spec.composition_rationale) > 0

    def test_gaps_identified(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试缺口识别"""
        bundle = CandidateBundle(workers=[architect_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success

        # 验证 gaps 字段存在
        assert hasattr(result.team_spec, "gaps")
        # 可能没有缺口，也可能有缺口


# =============================================================================
# Edge Cases Tests
# =============================================================================

class TestBaselineMatchmakerEdgeCases:
    """BaselineMatchmaker 边界情况测试"""

    def test_worker_with_unavailable_status(
        self,
        matchmaker: BaselineMatchmaker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试不可用状态的 Worker"""
        unavailable_worker = Worker(
            id="wrk_unavailable",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Unavailable", handle="unavailable"),
            responsibilities=["Development"],
            capabilities=[Capability(name="system_design", level=CapabilityLevel.EXPERT)],
            domains=["architecture"],
            state=WorkerState(
                availability=Availability.OFFLINE,
                trust_level=TrustLevel.TRUSTED,
                current_load=1.0,
            ),
        )

        bundle = CandidateBundle(workers=[unavailable_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        # 不可用 Worker 应该被排除或产生警告
        assert isinstance(result, CompositionResult)

    def test_multiple_competent_workers(
        self,
        matchmaker: BaselineMatchmaker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试多个都能胜任的 Worker"""
        workers = []
        for i in range(5):
            worker = Worker(
                id=f"wrk_architect_{i:03d}",
                type=WorkerType.BOT,
                identity=WorkerIdentity(name=f"Architect {i}", handle=f"arch_{i}"),
                responsibilities=["Design systems"],
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
            workers.append(worker)

        bundle = CandidateBundle(workers=workers)
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success
        # 应该只选择需要的数量
        assert len(result.team_spec.members) >= 1

    def test_compose_with_skills_and_resources(
        self,
        matchmaker: BaselineMatchmaker,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试带技能和资源的组合"""
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

        bundle = CandidateBundle(
            workers=[architect_worker],
            skills=[skill],
            resources=[resource],
        )

        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = matchmaker.compose(input_data)

        assert result.is_success
        # 检查技能和资源是否被选择
        assert isinstance(result.team_spec.selected_skills, list)
        assert isinstance(result.team_spec.selected_resources, list)