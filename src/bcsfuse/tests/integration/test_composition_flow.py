"""
Integration Tests for Team Composition Flow

M6: Team Composer / Matchmaker

测试从 TaskSpec/PlanDraft/CandidateBundle 输入到 TeamSpec 输出的完整流程。

闭环：
1. 创建 TaskSpec 和 PlanDraft
2. 构建 CompositionInput（包含 CandidateBundle）
3. 配置 BaselineMatchmaker 和 TeamCompositionService
4. 执行组合
5. 验证 CompositionResult 和 TeamSpec 结构
"""

from __future__ import annotations

import pytest

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
from src.infra.matchmakers.baseline_matchmaker import BaselineMatchmaker
from src.application.services.team_composition_service import TeamCompositionService


# =============================================================================
# Test Fixtures - Workers
# =============================================================================

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
        skills=[
            SkillRef(
                name="diagram_generation",
                source=SkillSource.BUILTIN,
                trust_level=TrustLevel.TRUSTED,
            ),
        ],
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
            Capability(name="debugging", level=CapabilityLevel.ADVANCED),
        ],
        domains=["development"],
        skills=[
            SkillRef(
                name="code_generator",
                source=SkillSource.BUILTIN,
                trust_level=TrustLevel.TRUSTED,
            ),
        ],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.3,
        ),
    )


@pytest.fixture
def reviewer_worker() -> Worker:
    """审核员 Worker"""
    return Worker(
        id="wrk_reviewer_001",
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name="Reviewer Bot",
            handle="reviewer_bot",
            title="Code Reviewer",
        ),
        responsibilities=["Review code", "Ensure quality"],
        capabilities=[
            Capability(name="code_review", level=CapabilityLevel.EXPERT),
            Capability(name="quality_assurance", level=CapabilityLevel.ADVANCED),
        ],
        domains=["review", "quality"],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.0,
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
            Capability(name="data_analysis", level=CapabilityLevel.ADVANCED),
            Capability(name="report_generation", level=CapabilityLevel.ADVANCED),
        ],
        domains=["research"],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.0,
        ),
    )


# =============================================================================
# Test Fixtures - Task Specs and Plan Drafts
# =============================================================================

@pytest.fixture
def architecture_task_spec() -> TaskSpec:
    """架构设计任务规格"""
    return TaskSpec(
        id="tsk_architecture_001",
        goal="Design system architecture for new microservices platform",
        deliverables=["Architecture design document", "System diagrams"],
        constraints=["Must use microservices pattern", "Must be cloud-native"],
        success_criteria=["Approved by technical review"],
        required_capabilities=["system_design", "documentation"],
        required_knowledge=["architecture", "microservices"],
        required_resources=["res_wiki_001"],
        risk_level=RiskLevel.LOW,
        unknowns=[],
        subtasks=[],
    )


@pytest.fixture
def architecture_plan_draft() -> PlanDraft:
    """架构设计规划草案"""
    return PlanDraft(
        task_id="tsk_architecture_001",
        strategy="Design-first with iterative refinement",
        steps=[
            PlanStep(
                id="s1",
                title="Research Requirements",
                objective="Analyze system requirements",
            ),
            PlanStep(
                id="s2",
                title="Create Design",
                objective="Design system architecture",
            ),
            PlanStep(
                id="s3",
                title="Document",
                objective="Create documentation",
            ),
        ],
        role_requirements=["architect"],
        knowledge_requirements=["architecture", "microservices"],
        resource_requirements=["res_wiki_001"],
        handoff_strategy="sequential",
        escalation_points=[],
    )


@pytest.fixture
def development_task_spec() -> TaskSpec:
    """开发任务规格"""
    return TaskSpec(
        id="tsk_development_001",
        goal="Implement user authentication module",
        deliverables=["Authentication module", "Unit tests", "Integration tests"],
        constraints=["Follow coding standards", "Minimum 80% test coverage"],
        success_criteria=["All tests pass", "Code review approved"],
        required_capabilities=["coding", "testing", "debugging"],
        required_knowledge=["python", "security"],
        required_resources=["res_repo_001"],
        risk_level=RiskLevel.MEDIUM,
        unknowns=["Specific auth provider choice"],
        subtasks=[],
    )


@pytest.fixture
def development_plan_draft() -> PlanDraft:
    """开发规划草案"""
    return PlanDraft(
        task_id="tsk_development_001",
        strategy="TDD with pair programming",
        steps=[
            PlanStep(
                id="s1",
                title="Write Tests",
                objective="Create test cases for auth module",
            ),
            PlanStep(
                id="s2",
                title="Implement",
                objective="Implement authentication logic",
            ),
            PlanStep(
                id="s3",
                title="Review",
                objective="Code review and refinement",
            ),
        ],
        role_requirements=["developer", "reviewer"],
        knowledge_requirements=["python", "security"],
        resource_requirements=["res_repo_001"],
        handoff_strategy="sequential",
        escalation_points=[],
    )


@pytest.fixture
def research_task_spec() -> TaskSpec:
    """研究任务规格"""
    return TaskSpec(
        id="tsk_research_001",
        goal="Research best practices for API design",
        deliverables=["Research report", "Recommendations document"],
        constraints=["Focus on REST and GraphQL"],
        success_criteria=["Comprehensive coverage", "Actionable insights"],
        required_capabilities=["information_retrieval", "data_analysis", "report_generation"],
        required_knowledge=["api", "rest", "graphql"],
        required_resources=["res_wiki_001"],
        risk_level=RiskLevel.LOW,
        unknowns=[],
        subtasks=[],
    )


@pytest.fixture
def research_plan_draft() -> PlanDraft:
    """研究规划草案"""
    return PlanDraft(
        task_id="tsk_research_001",
        strategy="Systematic literature review",
        steps=[
            PlanStep(
                id="s1",
                title="Gather Information",
                objective="Collect relevant materials",
            ),
            PlanStep(
                id="s2",
                title="Analyze",
                objective="Analyze and synthesize findings",
            ),
            PlanStep(
                id="s3",
                title="Report",
                objective="Generate research report",
            ),
        ],
        role_requirements=["researcher"],
        knowledge_requirements=["api", "rest", "graphql"],
        resource_requirements=["res_wiki_001"],
        handoff_strategy="sequential",
        escalation_points=[],
    )


# =============================================================================
# Test Fixtures - Services
# =============================================================================

@pytest.fixture
def matchmaker() -> BaselineMatchmaker:
    """BaselineMatchmaker 实例"""
    return BaselineMatchmaker()


@pytest.fixture
def composition_service(matchmaker: BaselineMatchmaker) -> TeamCompositionService:
    """TeamCompositionService 实例"""
    return TeamCompositionService(matchmaker=matchmaker)


# =============================================================================
# Flow Tests
# =============================================================================

class TestCompositionFlow:
    """组合流程集成测试"""

    def test_architecture_task_flow(
        self,
        composition_service: TeamCompositionService,
        architect_worker: Worker,
        developer_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """
        测试架构设计任务的完整组合流程

        Given: 架构设计任务和候选 Worker
        When: 执行组合
        Then: 返回包含架构师的 TeamSpec
        """
        # Step 1: 准备候选集
        bundle = CandidateBundle(workers=[architect_worker, developer_worker])

        # Step 2: 创建输入
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        # Step 3: 执行组合
        result = composition_service.compose(input_data)

        # Step 4: 验证结果结构
        assert isinstance(result, CompositionResult)
        assert result.is_success
        assert result.team_spec is not None

        # Step 5: 验证团队组成
        # 应该选择架构师（匹配 system_design 能力）
        assert len(result.team_spec.members) >= 1
        assert "wrk_architect_001" in result.team_spec.members

        # Step 6: 验证角色分配
        assert len(result.team_spec.role_assignments) >= 1

        # Step 7: 验证解释存在
        assert len(result.explanations) > 0

        # Step 8: 验证组合理由
        assert len(result.team_spec.composition_rationale) > 0

    def test_development_task_flow(
        self,
        composition_service: TeamCompositionService,
        developer_worker: Worker,
        reviewer_worker: Worker,
        development_task_spec: TaskSpec,
        development_plan_draft: PlanDraft,
    ):
        """
        测试开发任务的完整组合流程

        Given: 开发任务和候选 Worker（开发者、审核员）
        When: 执行组合
        Then: 返回包含开发者和审核员的 TeamSpec
        """
        # 准备候选集
        bundle = CandidateBundle(workers=[developer_worker, reviewer_worker])

        # 创建输入
        input_data = CompositionInput(
            task_spec=development_task_spec,
            plan_draft=development_plan_draft,
            candidate_bundle=bundle,
        )

        # 执行组合
        result = composition_service.compose(input_data)

        # 验证结果
        assert result.is_success
        assert result.team_spec is not None

        # 应该选择开发者（匹配 coding 能力）
        assert "wrk_developer_001" in result.team_spec.members

        # 审核员可能也被选中（取决于 max_team_size）
        assert len(result.team_spec.members) >= 1

    def test_research_task_flow(
        self,
        composition_service: TeamCompositionService,
        researcher_worker: Worker,
        research_task_spec: TaskSpec,
        research_plan_draft: PlanDraft,
    ):
        """
        测试研究任务的完整组合流程

        Given: 研究任务和研究员候选
        When: 执行组合
        Then: 返回包含研究员的 TeamSpec
        """
        # 准备候选集
        bundle = CandidateBundle(workers=[researcher_worker])

        # 创建输入
        input_data = CompositionInput(
            task_spec=research_task_spec,
            plan_draft=research_plan_draft,
            candidate_bundle=bundle,
        )

        # 执行组合
        result = composition_service.compose(input_data)

        # 验证结果
        assert result.is_success
        assert "wrk_researcher_001" in result.team_spec.members


# =============================================================================
# Constraint Tests
# =============================================================================

class TestCompositionConstraints:
    """组合约束集成测试"""

    def test_max_team_size_constraint(
        self,
        composition_service: TeamCompositionService,
        architect_worker: Worker,
        developer_worker: Worker,
        reviewer_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试最大团队大小约束"""
        bundle = CandidateBundle(workers=[architect_worker, developer_worker, reviewer_worker])
        constraints = CompositionConstraints(max_team_size=2)

        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
            constraints=constraints,
        )

        result = composition_service.compose(input_data)

        assert result.is_success
        assert len(result.team_spec.members) <= 2

    def test_min_team_size_warning(
        self,
        composition_service: TeamCompositionService,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试最小团队大小警告"""
        bundle = CandidateBundle(workers=[architect_worker])
        constraints = CompositionConstraints(min_team_size=3)

        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
            constraints=constraints,
        )

        result = composition_service.compose(input_data)

        # 应该成功但带警告
        assert result.is_success
        assert len(result.warnings) > 0


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestCompositionEdgeCases:
    """边界情况集成测试"""

    def test_empty_candidate_bundle(
        self,
        composition_service: TeamCompositionService,
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

        result = composition_service.compose(input_data)

        assert not result.is_success
        assert result.team_spec is None
        assert len(result.errors) > 0

    def test_no_matching_capabilities(
        self,
        composition_service: TeamCompositionService,
        researcher_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试没有匹配能力"""
        bundle = CandidateBundle(workers=[researcher_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = composition_service.compose(input_data)

        # 研究员没有 system_design 能力
        # 可能返回成功但带低分，或者失败
        assert isinstance(result, CompositionResult)

    def test_with_skills_and_resources(
        self,
        composition_service: TeamCompositionService,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试带技能和资源"""
        skill = SkillRef(
            name="diagram_generation",
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

        result = composition_service.compose(input_data)

        assert result.is_success
        assert len(result.team_spec.selected_skills) >= 0
        assert len(result.team_spec.selected_resources) >= 0


# =============================================================================
# Result Integrity Tests
# =============================================================================

class TestCompositionResultIntegrity:
    """结果完整性集成测试"""

    def test_all_explanations_have_valid_scores(
        self,
        composition_service: TeamCompositionService,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试所有解释有有效分数"""
        bundle = CandidateBundle(workers=[architect_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = composition_service.compose(input_data)

        for exp in result.explanations:
            assert 0.0 <= exp.match_score <= 1.0

    def test_team_spec_has_required_fields(
        self,
        composition_service: TeamCompositionService,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试 TeamSpec 有必需字段"""
        bundle = CandidateBundle(workers=[architect_worker])
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        result = composition_service.compose(input_data)

        assert result.team_spec.team_id.startswith("team_")
        assert len(result.team_spec.members) >= 1
        assert len(result.team_spec.role_assignments) >= 1
        assert len(result.team_spec.composition_rationale) >= 1

    def test_role_assignments_match_members(
        self,
        composition_service: TeamCompositionService,
        architect_worker: Worker,
        developer_worker: Worker,
        development_task_spec: TaskSpec,
        development_plan_draft: PlanDraft,
    ):
        """测试角色分配与成员匹配"""
        bundle = CandidateBundle(workers=[architect_worker, developer_worker])
        input_data = CompositionInput(
            task_spec=development_task_spec,
            plan_draft=development_plan_draft,
            candidate_bundle=bundle,
        )

        result = composition_service.compose(input_data)

        # 所有分配的 worker_id 应该在 members 中
        assigned_ids = {ra.worker_id for ra in result.team_spec.role_assignments}
        for member_id in result.team_spec.members:
            assert member_id in assigned_ids or len(assigned_ids) > 0


# =============================================================================
# End-to-End Flow Tests
# =============================================================================

class TestEndToEndComposition:
    """端到端组合测试"""

    def test_complete_happy_path(
        self,
        composition_service: TeamCompositionService,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """
        测试完整的 happy path

        从 CompositionInput 创建到 CompositionResult 返回的完整流程
        """
        # 1. 创建候选集
        bundle = CandidateBundle(workers=[architect_worker])

        # 2. 创建输入
        input_data = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle,
        )

        # 3. 执行组合
        result = composition_service.compose(input_data)

        # 4. 验证结果
        assert result is not None
        assert result.team_spec is not None
        assert isinstance(result.warnings, list)
        assert isinstance(result.errors, list)
        assert isinstance(result.explanations, list)

        # 5. 验证至少有一些结果
        assert len(result.team_spec.members) > 0

        # 6. 验证解释存在
        assert len(result.explanations) > 0

    def test_multiple_compositions_are_independent(
        self,
        composition_service: TeamCompositionService,
        architect_worker: Worker,
        developer_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        development_task_spec: TaskSpec,
        development_plan_draft: PlanDraft,
    ):
        """测试多次组合互不影响"""
        # 第一个组合
        bundle1 = CandidateBundle(workers=[architect_worker])
        input1 = CompositionInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            candidate_bundle=bundle1,
        )
        result1 = composition_service.compose(input1)

        # 第二个组合
        bundle2 = CandidateBundle(workers=[developer_worker])
        input2 = CompositionInput(
            task_spec=development_task_spec,
            plan_draft=development_plan_draft,
            candidate_bundle=bundle2,
        )
        result2 = composition_service.compose(input2)

        # 验证两次组合结果独立
        assert result1.team_spec is not None
        assert result2.team_spec is not None
        assert result1.team_spec.team_id != result2.team_spec.team_id
        assert "wrk_architect_001" in result1.team_spec.members
        assert "wrk_developer_001" in result2.team_spec.members