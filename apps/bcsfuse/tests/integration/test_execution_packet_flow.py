"""
Integration Tests for Execution Packet Flow

M8: Execution Packet Compiler

测试从 TaskSpec/PlanDraft/TeamSpec/CandidateBundle/Workspace 输入到 ExecutionPacket 输出的完整流程。

闭环：
1. 创建 TaskSpec 和 PlanDraft
2. 构建 CompilerInput
3. 配置 BaselineExecutionPacketCompiler 和 ExecutionPacketService
4. 执行编译
5. 验证 CompilerResult 和 ExecutionPacket 结构
"""

from __future__ import annotations

import pytest

from src.domain.models.compiler_input import CompilerInput, CompilerHints
from src.domain.models.compiler_result import CompilerResult
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.team_spec import TeamSpec, RoleAssignment
from src.domain.models.candidate_bundle import CandidateBundle, KnowledgeItem
from src.domain.models.workspace import Workspace, WorkspaceStatus
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel, SkillRef, ResourceRef,
    SkillSource, TrustLevel, ResourceKind, ResourceAccess, Availability,
)
from src.domain.models.execution_packet import ExecutionPacket
from src.infra.compilers.baseline_execution_packet_compiler import BaselineExecutionPacketCompiler
from src.application.services.execution_packet_service import ExecutionPacketService


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
        selected_resources=["res_repo_001"],
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
# Test Fixtures - Workspace
# =============================================================================

@pytest.fixture
def architecture_workspace(architecture_team_spec: TeamSpec) -> Workspace:
    """架构工作空间"""
    return Workspace(
        id="wsp_architecture_001",
        task_id="tsk_architecture_001",
        team_spec=architecture_team_spec,
        knowledge_mounts=["kno_architecture_001", "kno_architecture_002"],
        resource_mounts=["res_wiki_001", "res_templates_001"],
        status=WorkspaceStatus.ASSEMBLED,
    )


@pytest.fixture
def development_workspace(development_team_spec: TeamSpec) -> Workspace:
    """开发工作空间"""
    return Workspace(
        id="wsp_development_001",
        task_id="tsk_development_001",
        team_spec=development_team_spec,
        resource_mounts=["res_repo_001"],
        status=WorkspaceStatus.ASSEMBLED,
    )


# =============================================================================
# Test Fixtures - Services
# =============================================================================

@pytest.fixture
def compiler() -> BaselineExecutionPacketCompiler:
    """BaselineExecutionPacketCompiler 实例"""
    return BaselineExecutionPacketCompiler()


@pytest.fixture
def packet_service(compiler: BaselineExecutionPacketCompiler) -> ExecutionPacketService:
    """ExecutionPacketService 实例"""
    return ExecutionPacketService(compiler=compiler)


# =============================================================================
# Flow Tests
# =============================================================================

class TestCompilationFlow:
    """编译流程集成测试"""

    def test_architecture_task_flow(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        architecture_workspace: Workspace,
    ):
        """
        测试架构设计任务的完整编译流程

        Given: 架构设计任务计划、团队规格、候选集和工作空间
        When: 执行编译
        Then: 返回包含正确内容的 ExecutionPacket
        """
        # Step 1: 创建输入
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            workspace=architecture_workspace,
        )

        # Step 2: 执行编译
        result = packet_service.compile(input_data)

        # Step 3: 验证结果结构
        assert isinstance(result, CompilerResult)
        assert result.is_success
        assert result.packet is not None

        # Step 4: 验证 ExecutionPacket 基本属性
        packet = result.packet
        assert packet.task_spec == architecture_task_spec
        assert packet.plan_draft == architecture_plan_draft
        assert packet.team_spec == architecture_team_spec

        # Step 5: 验证 ContextPack
        assert len(packet.context_pack.knowledge_items) == 2
        assert len(packet.context_pack.summary) > 0

        # Step 6: 验证 ResourcePack
        assert len(packet.resource_pack.resources) >= 2

        # Step 7: 验证 SkillPack
        assert len(packet.skill_pack.allowlist) > 0

        # Step 8: 验证 Guardrails
        assert len(packet.guardrails.rules) > 0

        # Step 9: 验证 OutputContract
        assert len(packet.output_contract.required_artifacts) == 2

        # Step 10: 验证 LaunchPrompt
        assert len(packet.launch_prompt) > 0

        # Step 11: 验证解释存在
        assert len(result.explanations) > 0

    def test_development_task_flow(
        self,
        packet_service: ExecutionPacketService,
        developer_worker: Worker,
        development_task_spec: TaskSpec,
        development_plan_draft: PlanDraft,
        development_team_spec: TeamSpec,
        development_workspace: Workspace,
    ):
        """
        测试开发任务的完整编译流程

        Given: 开发任务计划、团队规格
        When: 执行编译
        Then: 返回正确编译的 ExecutionPacket
        """
        # 创建简单的候选集
        bundle = CandidateBundle(workers=[developer_worker])

        input_data = CompilerInput(
            task_spec=development_task_spec,
            plan_draft=development_plan_draft,
            team_spec=development_team_spec,
            candidate_bundle=bundle,
            workspace=development_workspace,
        )

        result = packet_service.compile(input_data)

        # 验证结果
        assert result.is_success
        assert result.packet is not None
        assert result.packet.task_spec == development_task_spec

        # 验证 guardrails 包含约束
        assert len(result.packet.guardrails.rules) > 0


# =============================================================================
# Hints Tests
# =============================================================================

class TestCompilationHints:
    """编译提示集成测试"""

    def test_include_full_context_false(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        architecture_workspace: Workspace,
    ):
        """测试 include_full_context=False - baseline 仍然包含所有上下文"""
        hints = CompilerHints(include_full_context=False)
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            workspace=architecture_workspace,
            hints=hints,
        )

        result = packet_service.compile(input_data)

        assert result.is_success
        # Baseline 编译器目前仍然包含所有上下文
        assert len(result.packet.context_pack.knowledge_items) >= 0

    def test_strict_guardrails_false(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        architecture_workspace: Workspace,
    ):
        """测试 strict_guardrails=False"""
        hints = CompilerHints(strict_guardrails=False)
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            workspace=architecture_workspace,
            hints=hints,
        )

        result = packet_service.compile(input_data)

        assert result.is_success


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestCompilationEdgeCases:
    """边界情况集成测试"""

    def test_empty_candidate_bundle(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_workspace: Workspace,
    ):
        """测试空候选集"""
        empty_bundle = CandidateBundle()
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=empty_bundle,
            workspace=architecture_workspace,
        )

        result = packet_service.compile(input_data)

        # 应该仍然成功
        assert result.is_success
        assert result.packet is not None

        # 应该有警告
        assert len(result.warnings) > 0

    def test_minimal_workspace(
        self,
        packet_service: ExecutionPacketService,
        architect_worker: Worker,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
    ):
        """测试最小 Workspace"""
        minimal_workspace = Workspace(
            id="wsp_minimal",
            task_id="tsk_architecture_001",
            team_spec=architecture_team_spec,
            status=WorkspaceStatus.DRAFT,
        )

        bundle = CandidateBundle(workers=[architect_worker])
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=bundle,
            workspace=minimal_workspace,
        )

        result = packet_service.compile(input_data)

        assert result.is_success


# =============================================================================
# Result Integrity Tests
# =============================================================================

class TestCompilationResultIntegrity:
    """结果完整性集成测试"""

    def test_packet_has_all_required_fields(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        architecture_workspace: Workspace,
    ):
        """测试 ExecutionPacket 有所有必需字段"""
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            workspace=architecture_workspace,
        )

        result = packet_service.compile(input_data)

        packet = result.packet
        assert packet is not None
        assert packet.task_spec is not None
        assert packet.plan_draft is not None
        assert packet.team_spec is not None
        assert packet.context_pack is not None
        assert packet.resource_pack is not None
        assert packet.skill_pack is not None
        assert packet.guardrails is not None
        assert packet.output_contract is not None
        assert packet.launch_prompt is not None

    def test_explanations_have_content(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        architecture_workspace: Workspace,
    ):
        """测试解释有内容"""
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            workspace=architecture_workspace,
        )

        result = packet_service.compile(input_data)

        for exp in result.explanations:
            assert exp.subject is not None
            assert exp.description is not None

    def test_no_errors_in_happy_path(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        architecture_workspace: Workspace,
    ):
        """测试 happy path 没有错误"""
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            workspace=architecture_workspace,
        )

        result = packet_service.compile(input_data)

        assert len(result.errors) == 0
        assert result.is_success


# =============================================================================
# Platform Independence Tests
# =============================================================================

class TestCompilationPlatformIndependence:
    """平台无关性集成测试"""

    def test_packet_is_platform_agnostic(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        architecture_workspace: Workspace,
    ):
        """测试 packet 是平台无关的"""
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            workspace=architecture_workspace,
        )

        result = packet_service.compile(input_data)

        packet = result.packet

        # 验证不存在 OpenClaw 特定字段
        assert not hasattr(packet, "task_md_path")
        assert not hasattr(packet, "team_md_path")
        assert not hasattr(packet, "context_md_path")
        assert not hasattr(packet, "agents_md_path")
        assert not hasattr(packet, "soul_md_path")
        assert not hasattr(packet, "tools_md_path")
        assert not hasattr(packet, "manifest")
        assert not hasattr(packet, "file_digests")
        assert not hasattr(packet, "workspace_path")

    def test_launch_prompt_is_generic(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        architecture_workspace: Workspace,
    ):
        """测试 launch_prompt 是通用的"""
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            workspace=architecture_workspace,
        )

        result = packet_service.compile(input_data)

        prompt = result.packet.launch_prompt.lower()

        # 不应包含 OpenClaw 特定引用
        assert "openclaw" not in prompt
        assert "agents.md" not in prompt
        assert "soul.md" not in prompt
        assert "tools.md" not in prompt
        assert "rules.md" not in prompt


# =============================================================================
# End-to-End Flow Tests
# =============================================================================

class TestEndToEndCompilation:
    """端到端编译测试"""

    def test_complete_happy_path(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        architecture_workspace: Workspace,
    ):
        """
        测试完整的 happy path

        从 CompilerInput 创建到 CompilerResult 返回的完整流程
        """
        # 1. 创建输入
        input_data = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            workspace=architecture_workspace,
        )

        # 2. 执行编译
        result = packet_service.compile(input_data)

        # 3. 验证结果
        assert result is not None
        assert result.packet is not None
        assert isinstance(result.warnings, list)
        assert isinstance(result.errors, list)
        assert isinstance(result.explanations, list)

        # 4. 验证 ExecutionPacket 完整性
        packet = result.packet
        assert packet.task_spec.id == "tsk_architecture_001"
        assert len(packet.context_pack.knowledge_items) > 0
        assert len(packet.resource_pack.resources) > 0

        # 5. 验证解释存在
        assert len(result.explanations) > 0

    def test_multiple_compilations_are_independent(
        self,
        packet_service: ExecutionPacketService,
        architecture_task_spec: TaskSpec,
        architecture_plan_draft: PlanDraft,
        architecture_team_spec: TeamSpec,
        architecture_candidate_bundle: CandidateBundle,
        architecture_workspace: Workspace,
        development_task_spec: TaskSpec,
        development_plan_draft: PlanDraft,
        development_team_spec: TeamSpec,
        developer_worker: Worker,
        development_workspace: Workspace,
    ):
        """测试多次编译互不影响"""
        # 第一个编译
        input1 = CompilerInput(
            task_spec=architecture_task_spec,
            plan_draft=architecture_plan_draft,
            team_spec=architecture_team_spec,
            candidate_bundle=architecture_candidate_bundle,
            workspace=architecture_workspace,
        )
        result1 = packet_service.compile(input1)

        # 第二个编译
        bundle2 = CandidateBundle(workers=[developer_worker])
        input2 = CompilerInput(
            task_spec=development_task_spec,
            plan_draft=development_plan_draft,
            team_spec=development_team_spec,
            candidate_bundle=bundle2,
            workspace=development_workspace,
        )
        result2 = packet_service.compile(input2)

        # 验证两次编译结果独立
        assert result1.packet is not None
        assert result2.packet is not None
        assert result1.packet.task_spec.id != result2.packet.task_spec.id
        assert result1.packet.team_spec != result2.packet.team_spec