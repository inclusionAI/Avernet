"""
Tests for TeamCompositionService

M6: Team Composer / Matchmaker

测试 TeamCompositionService 的服务层逻辑。
"""

from __future__ import annotations

import pytest

from src.application.services.team_composition_service import TeamCompositionService
from src.domain.services.matchmaker import Matchmaker
from src.domain.models.composition_input import CompositionInput
from src.domain.models.composition_result import CompositionResult
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.domain.models.candidate_bundle import CandidateBundle
from src.domain.models.worker import (
    Worker, WorkerState, WorkerType, WorkerIdentity,
    Capability, CapabilityLevel,
    Availability, TrustLevel,
)


# =============================================================================
# Mock Matchmaker for Testing
# =============================================================================

class MockMatchmaker:
    """用于测试的 Mock Matchmaker"""

    def __init__(self, result: CompositionResult = None):
        self._result = result
        self.compose_call_count = 0
        self.last_input = None

    def compose(self, input_data: CompositionInput) -> CompositionResult:
        """执行组合"""
        self.compose_call_count += 1
        self.last_input = input_data

        if self._result:
            return self._result

        # Default successful result
        from src.domain.models.team_spec import TeamSpec, RoleAssignment

        team_spec = TeamSpec(
            team_id="team_mock_001",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(
                    worker_id="wrk_001",
                    role="developer",
                    objective="Mock objective",
                ),
            ],
            composition_rationale=["Mock rationale"],
        )

        return CompositionResult(team_spec=team_spec, explanations=[])


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_matchmaker() -> MockMatchmaker:
    """Mock Matchmaker"""
    return MockMatchmaker()


@pytest.fixture
def composition_service(mock_matchmaker: MockMatchmaker) -> TeamCompositionService:
    """TeamCompositionService 实例"""
    return TeamCompositionService(matchmaker=mock_matchmaker)


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

class TestTeamCompositionServiceInit:
    """服务初始化测试"""

    def test_init_with_matchmaker(self, mock_matchmaker: MockMatchmaker):
        """测试使用 Matchmaker 初始化"""
        service = TeamCompositionService(matchmaker=mock_matchmaker)

        assert service.matchmaker is mock_matchmaker

    def test_service_has_matchmaker_property(self, composition_service: TeamCompositionService):
        """测试服务有 matchmaker 属性"""
        assert hasattr(composition_service, "matchmaker")
        assert composition_service.matchmaker is not None


# =============================================================================
# Compose Method Tests
# =============================================================================

class TestTeamCompositionServiceCompose:
    """compose 方法测试"""

    def test_compose_returns_result(
        self,
        composition_service: TeamCompositionService,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 compose 返回结果"""
        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
        )

        result = composition_service.compose(input_data)

        assert isinstance(result, CompositionResult)

    def test_compose_calls_matchmaker(
        self,
        composition_service: TeamCompositionService,
        mock_matchmaker: MockMatchmaker,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 compose 调用 Matchmaker"""
        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
        )

        composition_service.compose(input_data)

        assert mock_matchmaker.compose_call_count == 1
        assert mock_matchmaker.last_input == input_data

    def test_compose_passes_input_correctly(
        self,
        composition_service: TeamCompositionService,
        mock_matchmaker: MockMatchmaker,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试 compose 正确传递输入"""
        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
        )

        composition_service.compose(input_data)

        assert mock_matchmaker.last_input.task_spec == sample_task_spec
        assert mock_matchmaker.last_input.plan_draft == sample_plan_draft
        assert mock_matchmaker.last_input.candidate_bundle == sample_candidate_bundle


# =============================================================================
# Service Layer Integration Tests
# =============================================================================

class TestTeamCompositionServiceIntegration:
    """服务层集成测试"""

    def test_service_with_real_matchmaker(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试服务与真实 Matchmaker 集成"""
        from src.infra.matchmakers.baseline_matchmaker import BaselineMatchmaker

        matchmaker = BaselineMatchmaker()
        service = TeamCompositionService(matchmaker=matchmaker)

        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
        )

        result = service.compose(input_data)

        assert isinstance(result, CompositionResult)
        assert result.is_success
        assert result.team_spec is not None

    def test_service_handles_failed_composition(
        self,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
    ):
        """测试服务处理失败的组合"""
        from src.infra.matchmakers.baseline_matchmaker import BaselineMatchmaker

        matchmaker = BaselineMatchmaker()
        service = TeamCompositionService(matchmaker=matchmaker)

        # Empty bundle should result in failure
        empty_bundle = CandidateBundle()
        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=empty_bundle,
        )

        result = service.compose(input_data)

        assert isinstance(result, CompositionResult)
        assert not result.is_success
        assert len(result.errors) > 0


# =============================================================================
# Service Properties Tests
# =============================================================================

class TestTeamCompositionServiceProperties:
    """服务属性测试"""

    def test_matchmaker_property(
        self,
        composition_service: TeamCompositionService,
        mock_matchmaker: MockMatchmaker,
    ):
        """测试 matchmaker 属性"""
        assert composition_service.matchmaker is mock_matchmaker

    def test_service_is_stateless(
        self,
        composition_service: TeamCompositionService,
        sample_task_spec: TaskSpec,
        sample_plan_draft: PlanDraft,
        sample_candidate_bundle: CandidateBundle,
    ):
        """测试服务是无状态的"""
        input_data = CompositionInput(
            task_spec=sample_task_spec,
            plan_draft=sample_plan_draft,
            candidate_bundle=sample_candidate_bundle,
        )

        # Call compose multiple times
        result1 = composition_service.compose(input_data)
        result2 = composition_service.compose(input_data)

        # Results should be independent
        assert result1 is not result2 or result1 == result2