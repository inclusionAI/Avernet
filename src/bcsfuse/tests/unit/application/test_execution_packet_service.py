"""
Tests for ExecutionPacketService

M8: Execution Packet Compiler

测试 ExecutionPacketService 的服务层逻辑。
"""

from __future__ import annotations

import pytest

from src.application.services.execution_packet_service import ExecutionPacketService
from src.domain.services.execution_packet_compiler import ExecutionPacketCompiler
from src.domain.models.compiler_input import CompilerInput, CompilerHints
from src.domain.models.compiler_result import CompilerResult
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.team_spec import TeamSpec, RoleAssignment
from src.domain.models.candidate_bundle import CandidateBundle
from src.domain.models.workspace import Workspace, WorkspaceStatus
from src.domain.models.execution_packet import ExecutionPacket, ContextPack, ResourcePack, SkillPack, Guardrails, OutputContract
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel, Availability, TrustLevel,
)


# =============================================================================
# Mock ExecutionPacketCompiler for Testing
# =============================================================================

class MockExecutionPacketCompiler:
    """用于测试的 Mock ExecutionPacketCompiler"""

    def __init__(self, result: CompilerResult = None):
        self._result = result
        self.compile_call_count = 0
        self.last_input = None

    def compile(self, input_data: CompilerInput) -> CompilerResult:
        """执行编译"""
        self.compile_call_count += 1
        self.last_input = input_data

        if self._result:
            return self._result

        # Default successful result
        packet = ExecutionPacket(
            task_spec=input_data.task_spec,
            plan_draft=input_data.plan_draft,
            team_spec=input_data.team_spec,
            context_pack=ContextPack(summary="Mock context"),
            resource_pack=ResourcePack(),
            skill_pack=SkillPack(sandbox_required=False),
            guardrails=Guardrails(),
            output_contract=OutputContract(must_include_validation=True),
            launch_prompt="Mock prompt",
        )

        return CompilerResult(packet=packet)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_compiler() -> MockExecutionPacketCompiler:
    """Mock ExecutionPacketCompiler"""
    return MockExecutionPacketCompiler()


@pytest.fixture
def packet_service(mock_compiler: MockExecutionPacketCompiler) -> ExecutionPacketService:
    """ExecutionPacketService 实例"""
    return ExecutionPacketService(compiler=mock_compiler)


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
        steps=[PlanStep(id="s1", title="Step 1", objective="Test objective")],
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
def sample_candidate_bundle() -> CandidateBundle:
    """示例 CandidateBundle"""
    worker = Worker(
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
    return CandidateBundle(workers=[worker])


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
# Service Initialization Tests
# =============================================================================

class TestExecutionPacketServiceInit:
    """服务初始化测试"""

    def test_init_with_compiler(self, mock_compiler: MockExecutionPacketCompiler):
        """测试使用 Compiler 初始化"""
        service = ExecutionPacketService(compiler=mock_compiler)

        assert service.compiler is mock_compiler

    def test_service_has_compiler_property(self, packet_service: ExecutionPacketService):
        """测试服务有 compiler 属性"""
        assert hasattr(packet_service, "compiler")
        assert packet_service.compiler is not None


# =============================================================================
# Compile Method Tests
# =============================================================================

class TestExecutionPacketServiceCompile:
    """compile 方法测试"""

    def test_compile_returns_result(
        self,
        packet_service: ExecutionPacketService,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试 compile 返回结果"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = packet_service.compile(input_data)

        assert isinstance(result, CompilerResult)

    def test_compile_calls_compiler(
        self,
        packet_service: ExecutionPacketService,
        mock_compiler: MockExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试 compile 调用 Compiler"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        packet_service.compile(input_data)

        assert mock_compiler.compile_call_count == 1
        assert mock_compiler.last_input == input_data

    def test_compile_passes_input_correctly(
        self,
        packet_service: ExecutionPacketService,
        mock_compiler: MockExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试 compile 正确传递输入"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        packet_service.compile(input_data)

        assert mock_compiler.last_input.task_spec == sample_task_spec
        assert mock_compiler.last_input.plan_draft == sample_plan_draft
        assert mock_compiler.last_input.team_spec == sample_team_spec
        assert mock_compiler.last_input.candidate_bundle == sample_candidate_bundle
        assert mock_compiler.last_input.workspace == sample_workspace

    def test_compile_with_hints(
        self,
        packet_service: ExecutionPacketService,
        mock_compiler: MockExecutionPacketCompiler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试 compile 传递 hints"""
        hints = CompilerHints(
            include_full_context=False,
            strict_guardrails=False,
        )
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
            hints=hints,
        )

        packet_service.compile(input_data)

        assert mock_compiler.last_input.hints == hints


# =============================================================================
# Service Layer Integration Tests
# =============================================================================

class TestExecutionPacketServiceIntegration:
    """服务层集成测试"""

    def test_service_with_real_compiler(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试服务与真实 BaselineExecutionPacketCompiler 集成"""
        from src.infra.compilers.baseline_execution_packet_compiler import BaselineExecutionPacketCompiler

        compiler = BaselineExecutionPacketCompiler()
        service = ExecutionPacketService(compiler=compiler)

        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        result = service.compile(input_data)

        assert isinstance(result, CompilerResult)
        assert result.is_success
        assert result.packet is not None
        assert result.packet.task_spec == sample_task_spec

    def test_service_with_minimal_inputs(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
    ):
        """测试服务处理最小输入"""
        from src.infra.compilers.baseline_execution_packet_compiler import BaselineExecutionPacketCompiler

        compiler = BaselineExecutionPacketCompiler()
        service = ExecutionPacketService(compiler=compiler)

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
            candidate_bundle=CandidateBundle(),
            workspace=minimal_workspace,
        )

        result = service.compile(input_data)

        assert isinstance(result, CompilerResult)
        assert result.is_success


# =============================================================================
# Service Properties Tests
# =============================================================================

class TestExecutionPacketServiceProperties:
    """服务属性测试"""

    def test_compiler_property(
        self,
        packet_service: ExecutionPacketService,
        mock_compiler: MockExecutionPacketCompiler,
    ):
        """测试 compiler 属性"""
        assert packet_service.compiler is mock_compiler

    def test_service_is_stateless(
        self,
        packet_service: ExecutionPacketService,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
        sample_workspace: Workspace,
    ):
        """测试服务是无状态的"""
        input_data = CompilerInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            workspace=sample_workspace,
        )

        # Call compile multiple times
        result1 = packet_service.compile(input_data)
        result2 = packet_service.compile(input_data)

        # Results should be independent objects
        assert result1 is not result2
        assert result1.packet is not None
        assert result2.packet is not None