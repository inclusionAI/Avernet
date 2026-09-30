"""
Tests for RetrievalService

M5: Unified Retrieval Fabric

测试 RetrievalService 的行为，包括：
- 汇总检索结果
- warnings/errors 聚合
- TaskSpec + PlanDraft 驱动检索
- 空输入处理
"""

from __future__ import annotations

import pytest

from src.application.services.retrieval_service import RetrievalService
from src.domain.services.retriever import Retriever
from src.domain.models.retrieval_input import RetrievalInput, RetrievalFilters
from src.domain.models.retrieval_result import RetrievalResult, RetrievalExplanation
from src.domain.models.candidate_bundle import CandidateBundle
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from tests.fixtures.retrieval_data import (
    get_all_workers,
    get_all_knowledge_items,
    get_all_skill_refs,
    get_all_resource_refs,
    get_architecture_design_task_spec,
    get_architecture_plan_draft,
    get_research_task_spec,
    get_research_plan_draft,
)


# =============================================================================
# Mock Retriever for Testing
# =============================================================================

class MockRetriever:
    """Mock Retriever for testing"""

    def __init__(self, result: RetrievalResult):
        self._result = result
        self.retrieve_called = False
        self.last_input: RetrievalInput | None = None

    def retrieve(self, input_data: RetrievalInput) -> RetrievalResult:
        self.retrieve_called = True
        self.last_input = input_data
        return self._result


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def sample_result() -> RetrievalResult:
    """样本检索结果"""
    workers = get_all_workers()[:2]
    knowledge_items = get_all_knowledge_items()[:1]
    bundle = CandidateBundle(workers=workers, knowledge_items=knowledge_items)
    explanations = [
        RetrievalExplanation(
            candidate_type="worker",
            candidate_id=workers[0].id,
            matched_fields=["capabilities.system_design"],
            match_reason="Capability match",
            score=0.92,
        )
    ]
    return RetrievalResult(
        candidate_bundle=bundle,
        warnings=["Partial results"],
        errors=[],
        explanations=explanations,
    )


@pytest.fixture
def mock_retriever(sample_result: RetrievalResult) -> MockRetriever:
    """Mock Retriever"""
    return MockRetriever(result=sample_result)


@pytest.fixture
def retrieval_service(mock_retriever: MockRetriever) -> RetrievalService:
    """配置好的 RetrievalService"""
    return RetrievalService(retriever=mock_retriever)


# =============================================================================
# RetrievalService Tests
# =============================================================================

class TestRetrievalService:
    """RetrievalService 测试"""

    def test_service_has_retriever(self, retrieval_service: RetrievalService):
        """测试服务有 retriever"""
        assert retrieval_service.retriever is not None

    def test_retrieve_calls_retriever(
        self, retrieval_service: RetrievalService, mock_retriever: MockRetriever
    ):
        """测试 retrieve 调用 retriever"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        retrieval_service.retrieve(input_data)

        assert mock_retriever.retrieve_called
        assert mock_retriever.last_input == input_data

    def test_retrieve_returns_result(
        self, retrieval_service: RetrievalService
    ):
        """测试 retrieve 返回结果"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retrieval_service.retrieve(input_data)

        assert isinstance(result, RetrievalResult)
        assert result.candidate_bundle is not None

    def test_retrieve_with_filters(
        self, retrieval_service: RetrievalService, mock_retriever: MockRetriever
    ):
        """测试带过滤器的检索"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(worker_types=["bot"])
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft, filters=filters)

        retrieval_service.retrieve(input_data)

        assert mock_retriever.last_input is not None
        assert mock_retriever.last_input.filters == filters


# =============================================================================
# Warnings/Errors Aggregation Tests
# =============================================================================

class TestWarningsErrorsAggregation:
    """Warnings/Errors 聚合测试"""

    def test_warnings_aggregated(
        self, mock_retriever: MockRetriever
    ):
        """测试 warnings 聚合"""
        bundle = CandidateBundle()
        result_with_warnings = RetrievalResult(
            candidate_bundle=bundle,
            warnings=["Warning 1", "Warning 2"],
            errors=[],
        )
        mock_retriever._result = result_with_warnings
        service = RetrievalService(retriever=mock_retriever)

        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = service.retrieve(input_data)

        assert len(result.warnings) == 2
        assert "Warning 1" in result.warnings
        assert "Warning 2" in result.warnings

    def test_errors_aggregated(
        self, mock_retriever: MockRetriever
    ):
        """测试 errors 聚合"""
        bundle = CandidateBundle()
        result_with_errors = RetrievalResult(
            candidate_bundle=bundle,
            warnings=[],
            errors=["Error 1"],
        )
        mock_retriever._result = result_with_errors
        service = RetrievalService(retriever=mock_retriever)

        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = service.retrieve(input_data)

        assert len(result.errors) == 1
        assert "Error 1" in result.errors

    def test_both_warnings_and_errors(
        self, mock_retriever: MockRetriever
    ):
        """测试同时有 warnings 和 errors"""
        bundle = CandidateBundle()
        result_with_both = RetrievalResult(
            candidate_bundle=bundle,
            warnings=["Warning 1"],
            errors=["Error 1"],
        )
        mock_retriever._result = result_with_both
        service = RetrievalService(retriever=mock_retriever)

        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = service.retrieve(input_data)

        assert len(result.warnings) == 1
        assert len(result.errors) == 1


# =============================================================================
# TaskSpec + PlanDraft Driven Tests
# =============================================================================

class TestTaskSpecPlanDraftDriven:
    """TaskSpec + PlanDraft 驱动检索测试"""

    def test_task_spec_drives_retrieval(
        self, mock_retriever: MockRetriever
    ):
        """测试 TaskSpec 驱动检索"""
        service = RetrievalService(retriever=mock_retriever)

        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        service.retrieve(input_data)

        # Verify retriever was called with correct input
        assert mock_retriever.last_input is not None
        assert mock_retriever.last_input.task_spec.id == "tsk_arch_design_001"
        assert "system_design" in mock_retriever.last_input.task_spec.required_capabilities

    def test_plan_draft_drives_retrieval(
        self, mock_retriever: MockRetriever
    ):
        """测试 PlanDraft 驱动检索"""
        service = RetrievalService(retriever=mock_retriever)

        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        service.retrieve(input_data)

        # Verify retriever was called with correct input
        assert mock_retriever.last_input is not None
        assert mock_retriever.last_input.plan_draft.task_id == "tsk_arch_design_001"
        assert "researcher" in mock_retriever.last_input.plan_draft.role_requirements

    def test_research_task_driven_retrieval(
        self, mock_retriever: MockRetriever
    ):
        """测试调研任务驱动检索"""
        service = RetrievalService(retriever=mock_retriever)

        task_spec = get_research_task_spec()
        plan_draft = get_research_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        service.retrieve(input_data)

        assert mock_retriever.last_input is not None
        assert mock_retriever.last_input.task_spec.id == "tsk_research_001"
        assert "information_retrieval" in mock_retriever.last_input.task_spec.required_capabilities


# =============================================================================
# Empty Input Tests
# =============================================================================

class TestEmptyInput:
    """空输入处理测试"""

    def test_empty_task_spec_id_pattern(
        self, mock_retriever: MockRetriever
    ):
        """测试 TaskSpec ID 模式"""
        service = RetrievalService(retriever=mock_retriever)

        # TaskSpec has required pattern for id
        task_spec = TaskSpec(
            id="tsk_test_001",
            goal="Test goal",
            deliverables=["Deliverable"],
            constraints=[],
            success_criteria=["Success"],
            required_capabilities=["test_capability"],
            required_knowledge=[],
            required_resources=[],
            risk_level=RiskLevel.LOW,
            unknowns=[],
            subtasks=[],
        )
        plan_draft = PlanDraft(
            task_id="tsk_test_001",
            strategy="Test strategy",
            steps=[PlanStep(id="s1", title="Step", objective="Objective")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="test_handoff",
            escalation_points=[],
        )
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = service.retrieve(input_data)

        assert result is not None

    def test_retrieval_result_structure(
        self, mock_retriever: MockRetriever
    ):
        """测试检索结果结构"""
        service = RetrievalService(retriever=mock_retriever)

        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = service.retrieve(input_data)

        # Verify result structure
        assert hasattr(result, "candidate_bundle")
        assert hasattr(result, "warnings")
        assert hasattr(result, "errors")
        assert hasattr(result, "explanations")


# =============================================================================
# Service Interface Tests
# =============================================================================

class TestServiceInterface:
    """服务接口测试"""

    def test_service_implements_expected_interface(
        self, retrieval_service: RetrievalService
    ):
        """测试服务实现预期接口"""
        assert hasattr(retrieval_service, "retrieve")
        assert hasattr(retrieval_service, "retriever")

    def test_retrieve_accepts_retrieval_input(
        self, retrieval_service: RetrievalService
    ):
        """测试 retrieve 接受 RetrievalInput"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        # Should not raise
        result = retrieval_service.retrieve(input_data)
        assert result is not None

    def test_retrieve_returns_retrieval_result(
        self, retrieval_service: RetrievalService
    ):
        """测试 retrieve 返回 RetrievalResult"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retrieval_service.retrieve(input_data)

        assert isinstance(result, RetrievalResult)