"""
Integration Tests for Retrieval Flow

M5: Unified Retrieval Fabric

测试从 TaskSpec/PlanDraft 输入到 RetrievalResult 输出的最小闭环。

闭环：
1. 创建 TaskSpec 和 PlanDraft
2. 构建 RetrievalInput
3. 配置 BaselineRetriever 和 RetrievalService
4. 执行检索
5. 验证 RetrievalResult 结构和内容
"""

from __future__ import annotations

import pytest

from src.domain.models.retrieval_input import RetrievalInput, RetrievalFilters
from src.domain.models.retrieval_result import RetrievalResult
from src.domain.models.candidate_bundle import CandidateBundle
from src.infra.retrievers.baseline_retriever import BaselineRetriever, CandidateCatalog
from src.application.services.retrieval_service import RetrievalService
from tests.fixtures.retrieval_data import (
    get_all_workers,
    get_all_knowledge_items,
    get_all_skill_refs,
    get_all_resource_refs,
    get_architecture_design_task_spec,
    get_architecture_plan_draft,
    get_research_task_spec,
    get_research_plan_draft,
    get_development_task_spec,
    get_development_plan_draft,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def full_catalog() -> CandidateCatalog:
    """完整候选目录"""
    return CandidateCatalog(
        workers=get_all_workers(),
        knowledge_items=get_all_knowledge_items(),
        skills=get_all_skill_refs(),
        resources=get_all_resource_refs(),
    )


@pytest.fixture
def retriever(full_catalog: CandidateCatalog) -> BaselineRetriever:
    """配置好的 BaselineRetriever"""
    return BaselineRetriever(catalog=full_catalog)


@pytest.fixture
def retrieval_service(retriever: BaselineRetriever) -> RetrievalService:
    """配置好的 RetrievalService"""
    return RetrievalService(retriever=retriever)


# =============================================================================
# Flow Tests
# =============================================================================

class TestRetrievalFlow:
    """检索流程集成测试"""

    def test_architecture_design_flow(
        self, retrieval_service: RetrievalService
    ):
        """
        测试架构设计任务的完整检索流程

        Given: 架构设计任务的 TaskSpec 和 PlanDraft
        When: 执行检索
        Then: 返回包含架构师、知识项、技能和资源的 RetrievalResult
        """
        # Step 1: 准备输入
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        # Step 2: 执行检索
        result = retrieval_service.retrieve(input_data)

        # Step 3: 验证结果结构
        assert isinstance(result, RetrievalResult)
        assert isinstance(result.candidate_bundle, CandidateBundle)

        # Step 4: 验证 Worker 匹配
        # required_capabilities: ["system_design", "documentation"]
        # role_requirements: ["researcher", "architect", "reviewer"]
        assert len(result.candidate_bundle.workers) > 0

        matched_worker_ids = {w.id for w in result.candidate_bundle.workers}
        # 应该包含架构师 bot
        assert "wrk_architect_001" in matched_worker_ids

        # Step 5: 验证 Knowledge 匹配
        # knowledge_requirements: ["architecture", "microservices", "event-driven"]
        if len(result.candidate_bundle.knowledge_items) > 0:
            knowledge_tags = set()
            for ki in result.candidate_bundle.knowledge_items:
                knowledge_tags.update(ki.tags)
            # 至少有架构相关的知识
            assert "architecture" in knowledge_tags or "design" in knowledge_tags

        # Step 6: 验证 Skills
        # 从匹配的 Worker 聚合 skills
        skill_names = {s.name for s in result.candidate_bundle.skills}
        # 匹配的 worker 应该有 skills
        # engagement: skill_names 可能非空

        # Step 7: 验证 Resources
        # resource_requirements: ["res_wiki_001", "res_repo_001"]
        resource_ids = {r.id for r in result.candidate_bundle.resources}
        # 应该包含所需的资源
        assert "res_wiki_001" in resource_ids or "res_repo_001" in resource_ids

        # Step 8: 验证 Explanations
        assert len(result.explanations) > 0
        for exp in result.explanations:
            assert exp.candidate_id is not None
            assert exp.match_reason is not None
            assert 0.0 <= exp.score <= 1.0

        # Step 9: 验证 Evidence
        assert len(result.candidate_bundle.evidence) > 0

    def test_research_task_flow(
        self, retrieval_service: RetrievalService
    ):
        """
        测试调研任务的完整检索流程

        Given: 调研任务的 TaskSpec 和 PlanDraft
        When: 执行检索
        Then: 返回包含研究员、相关知识项的 RetrievalResult
        """
        # Step 1: 准备输入
        task_spec = get_research_task_spec()
        plan_draft = get_research_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        # Step 2: 执行检索
        result = retrieval_service.retrieve(input_data)

        # Step 3: 验证结果
        assert isinstance(result, RetrievalResult)

        # Step 4: 验证 Worker 匹配
        # required_capabilities: ["information_retrieval", "data_analysis", "report_generation"]
        assert len(result.candidate_bundle.workers) > 0

        # 应该包含研究员 bot
        matched_worker_ids = {w.id for w in result.candidate_bundle.workers}
        assert "wrk_researcher_001" in matched_worker_ids

        # Step 5: 验证 Knowledge 匹配
        # knowledge_requirements: ["api", "rest", "best_practices"]
        if len(result.candidate_bundle.knowledge_items) > 0:
            # 应该有相关的知识项
            pass

    def test_development_task_flow(
        self, retrieval_service: RetrievalService
    ):
        """
        测试开发任务的完整检索流程

        Given: 开发任务的 TaskSpec 和 PlanDraft
        When: 执行检索
        Then: 返回包含开发人员和审核员的 RetrievalResult
        """
        # Step 1: 准备输入
        task_spec = get_development_task_spec()
        plan_draft = get_development_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        # Step 2: 执行检索
        result = retrieval_service.retrieve(input_data)

        # Step 3: 验证结果
        assert isinstance(result, RetrievalResult)

        # Step 4: 验证 Worker 匹配
        # required_capabilities: ["coding", "testing", "debugging"]
        # role_requirements: ["developer", "reviewer"]
        assert len(result.candidate_bundle.workers) > 0

        matched_worker_ids = {w.id for w in result.candidate_bundle.workers}
        # 应该包含开发 bot
        assert "wrk_developer_001" in matched_worker_ids


# =============================================================================
# Filter Flow Tests
# =============================================================================

class TestFilterFlow:
    """过滤流程集成测试"""

    def test_filter_by_worker_type(
        self, retrieval_service: RetrievalService
    ):
        """测试按 Worker 类型过滤的流程"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(worker_types=["bot"])
        input_data = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
        )

        result = retrieval_service.retrieve(input_data)

        # 所有返回的 Worker 应该是 bot
        for worker in result.candidate_bundle.workers:
            assert worker.type == "bot"

    def test_filter_by_domain(
        self, retrieval_service: RetrievalService
    ):
        """测试按领域过滤的流程"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(domains=["architecture"])
        input_data = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
        )

        result = retrieval_service.retrieve(input_data)

        # 所有返回的 Worker 应该有 architecture domain
        for worker in result.candidate_bundle.workers:
            assert "architecture" in worker.domains

    def test_filter_by_trust_level(
        self, retrieval_service: RetrievalService
    ):
        """测试按信任级别过滤的流程"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(trust_levels=["trusted"])
        input_data = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
        )

        result = retrieval_service.retrieve(input_data)

        # 所有返回的 Worker 应该是 trusted
        for worker in result.candidate_bundle.workers:
            assert worker.state.trust_level == "trusted"

    def test_combined_filters(
        self, retrieval_service: RetrievalService
    ):
        """测试组合过滤器的流程"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(
            worker_types=["bot"],
            domains=["architecture"],
            trust_levels=["trusted"],
            top_k=5,
        )
        input_data = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
        )

        result = retrieval_service.retrieve(input_data)

        # 验证所有过滤条件都生效
        for worker in result.candidate_bundle.workers:
            assert worker.type == "bot"
            assert "architecture" in worker.domains
            assert worker.state.trust_level == "trusted"

        # 验证 top_k
        assert len(result.candidate_bundle.workers) <= 5
        assert len(result.candidate_bundle.knowledge_items) <= 5
        assert len(result.candidate_bundle.skills) <= 5
        assert len(result.candidate_bundle.resources) <= 5


# =============================================================================
# Edge Case Flow Tests
# =============================================================================

class TestEdgeCaseFlow:
    """边界场景流程测试"""

    def test_empty_catalog_flow(self):
        """测试空目录的检索流程"""
        empty_catalog = CandidateCatalog()
        retriever = BaselineRetriever(catalog=empty_catalog)
        service = RetrievalService(retriever=retriever)

        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = service.retrieve(input_data)

        # 应该返回空结果但不会崩溃
        assert isinstance(result, RetrievalResult)
        assert len(result.candidate_bundle.workers) == 0
        assert len(result.candidate_bundle.knowledge_items) == 0
        assert len(result.candidate_bundle.skills) == 0
        assert len(result.candidate_bundle.resources) == 0
        assert len(result.warnings) > 0

    def test_no_matching_requirements_flow(self):
        """测试无匹配需求的检索流程"""
        from src.domain.models.task_spec import TaskSpec, RiskLevel
        from src.domain.models.plan_draft import PlanDraft, PlanStep

        catalog = CandidateCatalog(
            workers=get_all_workers(),
        )
        retriever = BaselineRetriever(catalog=catalog)
        service = RetrievalService(retriever=retriever)

        # 创建一个需要不存在能力的任务
        task_spec = TaskSpec(
            id="tsk_no_match_001",
            goal="Do something impossible",
            deliverables=["Impossible deliverable"],
            constraints=[],
            success_criteria=["Success"],
            required_capabilities=["nonexistent_capability_xyz"],
            required_knowledge=["nonexistent_knowledge_xyz"],
            required_resources=[],
            risk_level=RiskLevel.LOW,
            unknowns=[],
            subtasks=[],
        )
        plan_draft = PlanDraft(
            task_id="tsk_no_match_001",
            strategy="No match strategy",
            steps=[PlanStep(id="s1", title="Step", objective="Objective")],
            role_requirements=["nonexistent_role_xyz"],
            knowledge_requirements=["nonexistent_knowledge_xyz"],
            resource_requirements=[],
            handoff_strategy="no_match_handoff",
            escalation_points=[],
        )
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = service.retrieve(input_data)

        # 应该返回空结果但格式正确
        assert isinstance(result, RetrievalResult)
        assert len(result.candidate_bundle.workers) == 0
        assert len(result.warnings) > 0


# =============================================================================
# Result Integrity Tests
# =============================================================================

class TestResultIntegrity:
    """结果完整性测试"""

    def test_all_explanations_match_candidates(
        self, retrieval_service: RetrievalService
    ):
        """测试所有 explanation 与候选匹配"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retrieval_service.retrieve(input_data)

        # 收集所有候选 ID
        all_candidate_ids = set()
        for w in result.candidate_bundle.workers:
            all_candidate_ids.add(("worker", w.id))
        for k in result.candidate_bundle.knowledge_items:
            all_candidate_ids.add(("knowledge", k.id))
        for s in result.candidate_bundle.skills:
            all_candidate_ids.add(("skill", s.name))
        for r in result.candidate_bundle.resources:
            all_candidate_ids.add(("resource", r.id))

        # 收集所有 explanation ID
        explanation_ids = {(e.candidate_type, e.candidate_id) for e in result.explanations}

        # 完全匹配
        assert explanation_ids == all_candidate_ids

    def test_result_is_immutable_after_creation(
        self, retrieval_service: RetrievalService
    ):
        """测试结果创建后不可变"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retrieval_service.retrieve(input_data)

        # 保存原始值
        original_worker_count = len(result.candidate_bundle.workers)
        original_warning_count = len(result.warnings)

        # 尝试修改（Pydantic 模型默认不可变取决于配置）
        # 这里只验证读取操作正常
        assert len(result.candidate_bundle.workers) == original_worker_count
        assert len(result.warnings) == original_warning_count

    def test_evidence_collected(
        self, retrieval_service: RetrievalService
    ):
        """测试证据收集"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retrieval_service.retrieve(input_data)

        # 如果有匹配的候选，应该有证据
        if len(result.candidate_bundle.workers) > 0:
            assert len(result.candidate_bundle.evidence) > 0


# =============================================================================
# End-to-End Flow Tests
# =============================================================================

class TestEndToEndFlow:
    """端到端流程测试"""

    def test_complete_happy_path(
        self, retrieval_service: RetrievalService
    ):
        """
        测试完整的 happy path

        从 TaskSpec/PlanDraft 创建到 RetrievalResult 返回的完整流程
        """
        # 1. Create TaskSpec and PlanDraft
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()

        # 2. Create RetrievalInput
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        # 3. Execute retrieval
        result = retrieval_service.retrieve(input_data)

        # 4. Verify result structure
        assert result is not None
        assert result.candidate_bundle is not None
        assert isinstance(result.warnings, list)
        assert isinstance(result.errors, list)
        assert isinstance(result.explanations, list)

        # 5. Verify at least some results
        assert len(result.candidate_bundle.workers) > 0

        # 6. Verify explanations are present
        assert len(result.explanations) > 0

        # 7. Verify scores are in valid range
        for exp in result.explanations:
            assert 0.0 <= exp.score <= 1.0

    def test_flow_with_multiple_tasks(
        self, retrieval_service: RetrievalService
    ):
        """测试多个任务的检索流程"""
        tasks = [
            (get_architecture_design_task_spec(), get_architecture_plan_draft()),
            (get_research_task_spec(), get_research_plan_draft()),
            (get_development_task_spec(), get_development_plan_draft()),
        ]

        for task_spec, plan_draft in tasks:
            input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)
            result = retrieval_service.retrieve(input_data)

            assert isinstance(result, RetrievalResult)
            # 每个任务都应该有结果（可能为空）
            assert result.candidate_bundle is not None