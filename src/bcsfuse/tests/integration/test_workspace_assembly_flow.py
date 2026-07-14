"""
Integration Tests for Workspace Assembly Flow

M7: Workspace / Group Assembly

测试从 TaskSpec/PlanDraft/TeamSpec/CandidateBundle 输入到 Workspace 输出的完整流程。

闭环：
1. 创建 TaskSpec、PlanDraft 和 TeamSpec
2. 构建 WorkspaceAssemblyInput（包含 CandidateBundle）
3. 配置 BaselineWorkspaceAssembler 和 WorkspaceAssemblyService
4. 执行组装
5. 验证 WorkspaceAssemblyResult 和 Workspace 结构
"""

from __future__ import annotations

import pytest

from src.domain.models.workspace_assembly_input import WorkspaceAssemblyInput, AssemblyHints
from src.domain.models.workspace_assembly_result import WorkspaceAssemblyResult
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.team_spec import TeamSpec, RoleAssignment
from src.domain.models.candidate_bundle import CandidateBundle, KnowledgeItem
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel, SkillRef, ResourceRef,
    SkillSource, TrustLevel, ResourceKind, ResourceAccess, Availability,
)
from src.domain.models.workspace import Workspace, WorkspaceStatus
from src.infra.assemblers.baseline_workspace_assembler import BaselineWorkspaceAssembler
from src.application.services.workspace_assembly_service import WorkspaceAssemblyService


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
        ],
        domains=["development"],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            current_load=0.3,
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
def architecture_team_spec() -> TeamSpec:
    """架构设计团队规格"""
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
        selected_skills=["diagram_generation"],
        selected_resources=["res_wiki_001"],
        composition_rationale=["Best match for architecture task"],
        gaps=[],
    )


@pytest.fixture
def development_task_spec() -> TaskSpec:
    """开发任务规格"""
    return TaskSpec(
        id="tsk_development_001",
        goal="Implement user authentication module",
        deliverables=["Authentication module", "Unit tests"],
        constraints=["Follow coding standards"],
        success_criteria=["All tests pass"],
        required_capabilities=["coding", "testing"],
        required_knowledge=["python", "security"],
        required_resources=["res_repo_001"],
        risk_level=RiskLevel.MEDIUM,
        unknowns=[],
        subtasks=[],
    )


@pytest.fixture
def development_plan_draft() -> PlanDraft:
    """开发规划草案"""
    return PlanDraft(
        task_id="tsk_development_001",
        strategy="TDD approach",
        steps=[
            PlanStep(
                id="s1",
                title="Write Tests",
                objective="Create test cases",
            ),
            PlanStep(
                id="s2",
                title="Implement",
                objective="Implement logic",
            ),
        ],
        role_requirements=["developer"],
        knowledge_requirements=["python", "security"],
        resource_requirements=["res_repo_001"],
        handoff_strategy="sequential",
        escalation_points=[],
    )


@pytest.fixture
def development_team_spec() -> TeamSpec:
    """开发团队规格"""
    return TeamSpec(
        team_id="team_development_001",
        members=["wrk_developer_001"],
        role_assignments=[
            RoleAssignment(
                worker_id="wrk_developer_001",
                role="developer",
                objective="Implement features",
            ),
        ],
        selected_resources=["res_repo_001", "res_ci_001"],
        composition_rationale=["Best developer match"],
        gaps=[],
    )


# =============================================================================
# Test Fixtures - Candidate Bundle
# =============================================================================

@pytest.fixture
def architecture_knowledge_items() -> list[KnowledgeItem]:
    """架构相关知识项"""
    return [
        KnowledgeItem(
            id="kno_architecture_001",
            kind="guide",
            title="Microservices Architecture Guide",
            summary="Best practices for microservices design",
            freshness="fresh",
            reliability="high",
            tags=["architecture", "microservices"],
        ),
        KnowledgeItem(
            id="kno_architecture_002",
            kind="doc",
            title="Cloud Native Patterns",
            summary="Common cloud native patterns",
            freshness="fresh",
            reliability="high",
            tags=["cloud", "patterns"],
        ),
    ]


@pytest.fixture
def architecture_resources() -> list[ResourceRef]:
    """架构相关资源"""
    return [
        ResourceRef(
            id="res_wiki_001",
            kind=ResourceKind.FILE,
            name="Architecture Wiki",
            access=ResourceAccess.READ,
        ),
        ResourceRef(
            id="res_templates_001",
            kind=ResourceKind.FILE,
            name="Design Templates",
            access=ResourceAccess.READ,
        ),
    ]


@pytest.fixture
def architecture_candidate_bundle(
    architect_worker: Worker,
    architecture_knowledge_items: list[KnowledgeItem],
    architecture_resources: list[ResourceRef],
) -> CandidateBundle:
    """架构任务候选集"""
    return CandidateBundle(
        workers=[architect_worker],
        knowledge_items=architecture_knowledge_items,
        resources=architecture_resources,
    )


# =============================================================================
# Test Fixtures - Services
# =============================================================================

@pytest.fixture
def assembler() -> BaselineWorkspaceAssembler:
    """BaselineWorkspaceAssembler 实例"""
    return BaselineWorkspaceAssembler()


@pytest.fixture
def assembly_service(assembler: BaselineWorkspaceAssembler) -> WorkspaceAssemblyService:
    """WorkspaceAssemblyService 实例"""
    return WorkspaceAssemblyService(assembler=assembler)


# =============================================================================
# Flow Tests
# =============================================================================

class TestAssemblyFlow:
    """组装流程集成测试"""

    def test_architecture_task_flow(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
    ):
        """
        测试架构设计任务的完整组装流程

        Given: 架构设计任务计划、团队规格和候选集
        When: 执行组装
        Then: 返回包含正确挂载的 Workspace
        """
        # Step 1: 创建输入
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
        )

        # Step 2: 执行组装
        result = assembly_service.assemble(input_data)

        # Step 3: 验证结果结构
        assert isinstance(result, WorkspaceAssemblyResult)
        assert result.is_success
        assert result.workspace is not None

        # Step 4: 验证 Workspace 基本属性
        workspace = result.workspace
        assert workspace.id.startswith("wsp_")
        assert workspace.task_id == architecture_task_spec.id
        assert workspace.team_spec == architecture_team_spec
        assert workspace.status == WorkspaceStatus.ASSEMBLED.value

        # Step 5: 验证知识挂载
        assert len(workspace.knowledge_mounts) == 2
        assert "kno_architecture_001" in workspace.knowledge_mounts
        assert "kno_architecture_002" in workspace.knowledge_mounts

        # Step 6: 验证资源挂载（来自 bundle + team_spec）
        assert len(workspace.resource_mounts) >= 2

        # Step 7: 验证事件
        assert len(workspace.events) > 0
        event_types = [e.type for e in workspace.events]
        assert "workspace_created" in event_types

        # Step 8: 验证解释
        assert len(result.explanations) > 0

        # Step 9: 验证挂载信息
        assert len(result.mount_info) > 0

    def test_development_task_flow(
        self,
        assembly_service: WorkspaceAssemblyService,
        developer_worker: Worker,
        development_task_spec: TaskSpec,
        development_plan_draft: PlanDraft,
        development_team_spec: TeamSpec,
    ):
        """
        测试开发任务的完整组装流程

        Given: 开发任务计划、团队规格
        When: 执行组装
        Then: 返回正确组装的 Workspace
        """
        # 创建简单的候选集
        bundle = CandidateBundle(workers=[developer_worker])

        input_data = WorkspaceAssemblyInput(
            task_spec=development_task_spec,
            plan_draft=development_plan_draft,
            team_spec=development_team_spec,
            candidate_bundle=bundle,
        )

        result = assembly_service.assemble(input_data)

        # 验证结果
        assert result.is_success
        assert result.workspace is not None
        assert result.workspace.task_id == development_task_spec.id

        # 资源应该从 TeamSpec.selected_resources 挂载
        assert "res_repo_001" in result.workspace.resource_mounts


# =============================================================================
# Hints Tests
# =============================================================================

class TestAssemblyHints:
    """组装提示集成测试"""

    def test_include_all_knowledge_false(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
    ):
        """测试 include_all_knowledge=False"""
        hints = AssemblyHints(include_all_knowledge=False)
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            hints=hints,
        )

        result = assembly_service.assemble(input_data)

        assert result.is_success
        assert len(result.workspace.knowledge_mounts) == 0

    def test_include_all_resources_false(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
    ):
        """测试 include_all_resources=False"""
        hints = AssemblyHints(include_all_resources=False)
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            hints=hints,
        )

        result = assembly_service.assemble(input_data)

        assert result.is_success
        # 应该仍然挂载 TeamSpec.selected_resources
        assert "res_wiki_001" in result.workspace.resource_mounts

    def test_custom_mount_paths(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
    ):
        """测试自定义挂载路径"""
        hints = AssemblyHints(
            custom_mount_paths={
                "kno_architecture_001": "/custom/knowledge/guide",
            }
        )
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            hints=hints,
        )

        result = assembly_service.assemble(input_data)

        assert result.is_success
        # 验证挂载信息包含自定义路径
        mount_ids = [m.id for m in result.mount_info]
        assert "kno_architecture_001" in mount_ids


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestAssemblyEdgeCases:
    """边界情况集成测试"""

    def test_empty_candidate_bundle(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
    ):
        """测试空候选集"""
        empty_bundle = CandidateBundle()
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=empty_bundle,
        )

        result = assembly_service.assemble(input_data)

        # 应该仍然成功
        assert result.is_success
        assert result.workspace is not None

        # 知识挂载为空
        assert len(result.workspace.knowledge_mounts) == 0

        # 资源从 TeamSpec 挂载
        assert len(result.workspace.resource_mounts) >= 1

    def test_minimal_team_spec(
        self,
        assembly_service: WorkspaceAssemblyService,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
    ):
        """测试最小 TeamSpec"""
        minimal_team = TeamSpec(
            team_id="team_minimal",
            members=["wrk_architect_001"],
            role_assignments=[
                RoleAssignment(
                    worker_id="wrk_architect_001",
                    role="contributor",
                    objective="Contribute",
                ),
            ],
            composition_rationale=["Minimal team"],
        )

        bundle = CandidateBundle(workers=[architect_worker])
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=minimal_team,
            candidate_bundle=bundle,
        )

        result = assembly_service.assemble(input_data)

        assert result.is_success
        assert result.workspace.team_spec == minimal_team


# =============================================================================
# Result Integrity Tests
# =============================================================================

class TestAssemblyResultIntegrity:
    """结果完整性集成测试"""

    def test_workspace_has_all_required_fields(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
    ):
        """测试 Workspace 有所有必需字段"""
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
        )

        result = assembly_service.assemble(input_data)

        workspace = result.workspace
        assert workspace.id is not None
        assert workspace.task_id is not None
        assert workspace.team_spec is not None
        assert workspace.knowledge_mounts is not None
        assert workspace.resource_mounts is not None
        assert workspace.artifacts is not None
        assert workspace.events is not None
        assert workspace.status is not None

    def test_mount_info_has_required_fields(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
    ):
        """测试 MountInfo 有必需字段"""
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
        )

        result = assembly_service.assemble(input_data)

        for mount in result.mount_info:
            assert mount.id is not None
            assert mount.type in ["knowledge", "resource"]
            assert mount.mount_reason is not None

    def test_explanations_have_content(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
    ):
        """测试解释有内容"""
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
        )

        result = assembly_service.assemble(input_data)

        for exp in result.explanations:
            assert exp.subject is not None
            assert exp.description is not None

    def test_no_warnings_or_errors_in_happy_path(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
    ):
        """测试 happy path 没有警告或错误"""
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
        )

        result = assembly_service.assemble(input_data)

        assert len(result.warnings) == 0
        assert len(result.errors) == 0
        assert result.is_success


# =============================================================================
# End-to-End Flow Tests
# =============================================================================

class TestEndToEndAssembly:
    """端到端组装测试"""

    def test_complete_happy_path(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
    ):
        """
        测试完整的 happy path

        从 WorkspaceAssemblyInput 创建到 WorkspaceAssemblyResult 返回的完整流程
        """
        # 1. 创建输入
        input_data = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
        )

        # 2. 执行组装
        result = assembly_service.assemble(input_data)

        # 3. 验证结果
        assert result is not None
        assert result.workspace is not None
        assert isinstance(result.warnings, list)
        assert isinstance(result.errors, list)
        assert isinstance(result.explanations, list)
        assert isinstance(result.mount_info, list)

        # 4. 验证 Workspace 完整性
        workspace = result.workspace
        assert workspace.id.startswith("wsp_")
        assert len(workspace.events) > 0
        assert len(workspace.knowledge_mounts) > 0

    def test_multiple_assemblies_are_independent(
        self,
        assembly_service: WorkspaceAssemblyService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        development_task_spec: TaskSpec,
        development_plan_draft: PlanDraft,
        development_team_spec: TeamSpec,
        developer_worker: Worker,
    ):
        """测试多次组装互不影响"""
        # 第一个组装
        input1 = WorkspaceAssemblyInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
        )
        result1 = assembly_service.assemble(input1)

        # 第二个组装
        bundle2 = CandidateBundle(workers=[developer_worker])
        input2 = WorkspaceAssemblyInput(
            task_spec=development_task_spec,
            plan_draft=development_plan_draft,
            team_spec=development_team_spec,
            candidate_bundle=bundle2,
        )
        result2 = assembly_service.assemble(input2)

        # 验证两次组装结果独立
        assert result1.workspace is not None
        assert result2.workspace is not None
        assert result1.workspace.id != result2.workspace.id
        assert result1.workspace.task_id != result2.workspace.task_id
        assert result1.workspace.team_spec != result2.workspace.team_spec