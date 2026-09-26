"""
Tests for WorkspaceAssemblyService

M7: Workspace / Group Assembly

测试 WorkspaceAssemblyService 的服务层逻辑。
"""

from __future__ import annotations

import pytest

from src.application.services.workspace_assembly_service import WorkspaceAssemblyService
from src.domain.services.workspace_assembler import WorkspaceAssembler
from src.domain.models.workspace_assembly_input import WorkspaceAssemblyInput, AssemblyHints
from src.domain.models.workspace_assembly_result import WorkspaceAssemblyResult
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.team_spec import TeamSpec, RoleAssignment
from src.domain.models.candidate_bundle import CandidateBundle
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel,
    Availability, TrustLevel,
)


# =============================================================================
# Mock WorkspaceAssembler for Testing
# =============================================================================

class MockWorkspaceAssembler:
    """用于测试的 Mock WorkspaceAssembler"""

    def __init__(self, result: WorkspaceAssemblyResult = None):
        self._result = result
        self.assemble_call_count = 0
        self.last_input = None

    def assemble(self, input_data: WorkspaceAssemblyInput) -> WorkspaceAssemblyResult:
        """执行组装"""
        self.assemble_call_count += 1
        self.last_input = input_data

        if self._result:
            return self._result

        # Default successful result
        from src.domain.models.workspace import Workspace, WorkspaceStatus

        workspace = Workspace(
            id="wsp_mock_001",
            task_id=input_data.task_spec.id,
            team_spec=input_data.team_spec,
            status=WorkspaceStatus.ASSEMBLED,
        )

        return WorkspaceAssemblyResult(workspace=workspace)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_assembler() -> MockWorkspaceAssembler:
    """Mock WorkspaceAssembler"""
    return MockWorkspaceAssembler()


@pytest.fixture
def assembly_service(mock_assembler: MockWorkspaceAssembler) -> WorkspaceAssemblyService:
    """WorkspaceAssemblyService 实例"""
    return WorkspaceAssemblyService(assembler=mock_assembler)


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


# =============================================================================
# Service Initialization Tests
# =============================================================================

class TestWorkspaceAssemblyServiceInit:
    """服务初始化测试"""

    def test_init_with_assembler(self, mock_assembler: MockWorkspaceAssembler):
        """测试使用 WorkspaceAssembler 初始化"""
        service = WorkspaceAssemblyService(assembler=mock_assembler)

        assert service.assembler is mock_assembler

    def test_service_has_assembler_property(self, assembly_service: WorkspaceAssemblyService):
        """测试服务有 assembler 属性"""
        assert hasattr(assembly_service, "assembler")
        assert assembly_service.assembler is not None


# =============================================================================
# Assemble Method Tests
# =============================================================================

class TestWorkspaceAssemblyServiceAssemble:
    """assemble 方法测试"""

    def test_assemble_returns_result(
        self,
        assembly_service: WorkspaceAssemblyService,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 assemble 返回结果"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = assembly_service.assemble(input_data)

        assert isinstance(result, WorkspaceAssemblyResult)

    def test_assemble_calls_assembler(
        self,
        assembly_service: WorkspaceAssemblyService,
        mock_assembler: MockWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 assemble 调用 WorkspaceAssembler"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        assembly_service.assemble(input_data)

        assert mock_assembler.assemble_call_count == 1
        assert mock_assembler.last_input == input_data

    def test_assemble_passes_input_correctly(
        self,
        assembly_service: WorkspaceAssemblyService,
        mock_assembler: MockWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 assemble 正确传递输入"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        assembly_service.assemble(input_data)

        assert mock_assembler.last_input.task_spec == sample_task_spec
        assert mock_assembler.last_input.plan_draft == sample_plan_draft
        assert mock_assembler.last_input.team_spec == sample_team_spec
        assert mock_assembler.last_input.candidate_bundle == sample_candidate_bundle

    def test_assemble_with_hints(
        self,
        assembly_service: WorkspaceAssemblyService,
        mock_assembler: MockWorkspaceAssembler,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 assemble 传递 hints"""
        hints = AssemblyHints(
            include_all_knowledge=False,
            include_all_resources=False,
        )
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
            hints=hints,
        )

        assembly_service.assemble(input_data)

        assert mock_assembler.last_input.hints == hints


# =============================================================================
# Service Layer Integration Tests
# =============================================================================

class TestWorkspaceAssemblyServiceIntegration:
    """服务层集成测试"""

    def test_service_with_real_assembler(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试服务与真实 BaselineWorkspaceAssembler 集成"""
        from src.infra.assemblers.baseline_workspace_assembler import BaselineWorkspaceAssembler

        assembler = BaselineWorkspaceAssembler()
        service = WorkspaceAssemblyService(assembler=assembler)

        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        result = service.assemble(input_data)

        assert isinstance(result, WorkspaceAssemblyResult)
        assert result.is_success
        assert result.workspace is not None
        assert result.workspace.task_id == sample_task_spec.id

    def test_service_with_minimal_inputs(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
    ):
        """测试服务处理最小输入"""
        from src.infra.assemblers.baseline_workspace_assembler import BaselineWorkspaceAssembler

        assembler = BaselineWorkspaceAssembler()
        service = WorkspaceAssemblyService(assembler=assembler)

        # Minimal team
        minimal_team = TeamSpec(
            team_id="team_minimal",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(
                    worker_id="wrk_001",
                    role="contributor",
                    objective="Contribute",
                ),
            ],
            composition_rationale=["Minimal"],
        )

        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=minimal_team,
            candidate_bundle=CandidateBundle(),
        )

        result = service.assemble(input_data)

        assert isinstance(result, WorkspaceAssemblyResult)
        assert result.is_success


# =============================================================================
# Service Properties Tests
# =============================================================================

class TestWorkspaceAssemblyServiceProperties:
    """服务属性测试"""

    def test_assembler_property(
        self,
        assembly_service: WorkspaceAssemblyService,
        mock_assembler: MockWorkspaceAssembler,
    ):
        """测试 assembler 属性"""
        assert assembly_service.assembler is mock_assembler

    def test_service_is_stateless(
        self,
        assembly_service: WorkspaceAssemblyService,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_team_spec: TeamSpec,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试服务是无状态的"""
        input_data = WorkspaceAssemblyInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            team_spec=sample_team_spec,
            candidate_bundle=sample_candidate_bundle,
        )

        # Call assemble multiple times
        result1 = assembly_service.assemble(input_data)
        result2 = assembly_service.assemble(input_data)

        # Results should be independent objects
        assert result1 is not result2
        assert result1.workspace is not None
        assert result2.workspace is not None