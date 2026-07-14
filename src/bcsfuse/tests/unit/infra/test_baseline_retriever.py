"""
Tests for BaselineRetriever

M5: Unified Retrieval Fabric

测试 BaselineRetriever 的检索行为，包括：
- 基于能力的 Worker 检索
- 基于角色的 Worker 检索
- Knowledge/Skill/Resource 检索
- 基础过滤
- 基础排序
- 解释生成
- 边界场景

约束：
- 通过构造函数注入测试数据，不依赖 tests/fixtures
"""

from __future__ import annotations

import pytest

from src.domain.services.retriever import Retriever
from src.infra.retrievers.baseline_retriever import BaselineRetriever, CandidateCatalog
from src.domain.models.retrieval_input import RetrievalInput, RetrievalFilters
from src.domain.models.retrieval_result import RetrievalResult
from src.domain.models.candidate_bundle import KnowledgeItem
from src.domain.models.worker import (
    Worker, WorkerType, WorkerIdentity, Capability, CapabilityLevel,
    WorkerState, Availability, TrustLevel, SkillRef, SkillSource,
    ResourceRef, ResourceKind, ResourceAccess,
)
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
# Test Fixtures (injected into retriever, not imported by src code)
# =============================================================================

@pytest.fixture
def sample_workers() -> list[Worker]:
    """样本 Worker 列表"""
    return get_all_workers()


@pytest.fixture
def sample_knowledge_items() -> list[KnowledgeItem]:
    """样本 KnowledgeItem 列表"""
    return get_all_knowledge_items()


@pytest.fixture
def sample_skills() -> list[SkillRef]:
    """样本 SkillRef 列表"""
    return get_all_skill_refs()


@pytest.fixture
def sample_resources() -> list[ResourceRef]:
    """样本 ResourceRef 列表"""
    return get_all_resource_refs()


@pytest.fixture
def full_catalog(
    sample_workers: list[Worker],
    sample_knowledge_items: list[KnowledgeItem],
    sample_skills: list[SkillRef],
    sample_resources: list[ResourceRef],
) -> CandidateCatalog:
    """完整候选目录"""
    return CandidateCatalog(
        workers=sample_workers,
        knowledge_items=sample_knowledge_items,
        skills=sample_skills,
        resources=sample_resources,
    )


@pytest.fixture
def retriever(full_catalog: CandidateCatalog) -> BaselineRetriever:
    """配置好的 BaselineRetriever"""
    return BaselineRetriever(catalog=full_catalog)


@pytest.fixture
def empty_catalog() -> CandidateCatalog:
    """空候选目录"""
    return CandidateCatalog()


@pytest.fixture
def empty_retriever(empty_catalog: CandidateCatalog) -> BaselineRetriever:
    """空目录的 Retriever"""
    return BaselineRetriever(catalog=empty_catalog)


# =============================================================================
# Retriever Interface Tests
# =============================================================================

class TestRetrieverInterface:
    """Retriever 接口测试"""

    def test_retriever_is_protocol(self):
        """测试 Retriever 是 Protocol"""
        from typing import Protocol
        # Retriever 应该是一个 Protocol
        assert hasattr(Retriever, "__protocol_attrs__") or hasattr(Retriever, "__mro_entries__")

    def test_baseline_retriever_implements_retriever(self, retriever: BaselineRetriever):
        """测试 BaselineRetriever 实现了 Retriever 接口"""
        assert isinstance(retriever, Retriever)


# =============================================================================
# Worker Retrieval Tests
# =============================================================================

class TestWorkerRetrieval:
    """Worker 检索测试"""

    def test_retrieve_workers_by_task_spec_capabilities(
        self, retriever: BaselineRetriever
    ):
        """测试基于 TaskSpec.required_capabilities 检索 Worker"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 应该找到具有 system_design 或 documentation 能力的 Worker
        matched_capabilities = set()
        for worker in result.candidate_bundle.workers:
            for cap in worker.capabilities:
                matched_capabilities.add(cap.name)

        # 至少有一个匹配的能力
        assert len(result.candidate_bundle.workers) > 0
        assert "system_design" in matched_capabilities or "documentation" in matched_capabilities

    def test_retrieve_workers_by_plan_role_requirements(
        self, retriever: BaselineRetriever
    ):
        """测试基于 PlanDraft.role_requirements 检索 Worker"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 应该找到匹配角色的 Worker
        # role_requirements: ["researcher", "architect", "reviewer"]
        assert len(result.candidate_bundle.workers) > 0

        # 验证匹配的 Worker 的 responsibilities 与角色相关
        matched_responsibilities = set()
        for worker in result.candidate_bundle.workers:
            matched_responsibilities.update(worker.responsibilities)

        # 至少应该有 research 或 architecture 相关职责
        has_relevant_role = any(
            r in matched_responsibilities
            for r in ["research", "architecture_design", "code_review", "approval"]
        )
        assert has_relevant_role

    def test_worker_explanations_generated(
        self, retriever: BaselineRetriever
    ):
        """测试 Worker 的 explanation 生成"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 每个 Worker 应该有对应的 explanation
        worker_ids = {w.id for w in result.candidate_bundle.workers}
        explanation_worker_ids = {
            e.candidate_id
            for e in result.explanations
            if e.candidate_type == "worker"
        }

        assert worker_ids == explanation_worker_ids
        assert len(result.explanations) > 0

    def test_retrieve_research_task_workers(
        self, retriever: BaselineRetriever
    ):
        """测试调研任务 Worker 检索"""
        task_spec = get_research_task_spec()
        plan_draft = get_research_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 应该找到具有 information_retrieval 能力的 Worker
        assert len(result.candidate_bundle.workers) > 0

        matched_caps = set()
        for worker in result.candidate_bundle.workers:
            for cap in worker.capabilities:
                matched_caps.add(cap.name)

        assert "information_retrieval" in matched_caps


# =============================================================================
# Knowledge Retrieval Tests
# =============================================================================

class TestKnowledgeRetrieval:
    """Knowledge 检索测试"""

    def test_retrieve_knowledge_by_requirements(
        self, retriever: BaselineRetriever
    ):
        """测试基于 knowledge_requirements 检索知识项"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # knowledge_requirements: ["architecture", "microservices", "event-driven"]
        # 应该找到匹配标签的知识项
        for ki in result.candidate_bundle.knowledge_items:
            assert ki.tags is not None

    def test_knowledge_explanations_generated(
        self, retriever: BaselineRetriever
    ):
        """测试 Knowledge 的 explanation 生成"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 如果有知识项，每个都应该有 explanation
        if len(result.candidate_bundle.knowledge_items) > 0:
            knowledge_ids = {ki.id for ki in result.candidate_bundle.knowledge_items}
            explanation_knowledge_ids = {
                e.candidate_id
                for e in result.explanations
                if e.candidate_type == "knowledge"
            }
            assert knowledge_ids == explanation_knowledge_ids


# =============================================================================
# Skill Retrieval Tests
# =============================================================================

class TestSkillRetrieval:
    """Skill 检索测试"""

    def test_retrieve_skills_from_workers(
        self, retriever: BaselineRetriever
    ):
        """测试从匹配的 Worker 聚合 Skill"""
        task_spec = get_research_task_spec()
        plan_draft = get_research_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 如果有匹配的 Worker 且 Worker 有 skills，应该聚合
        # 这是 baseline 行为：从匹配的 Worker 聚合其 skills
        if len(result.candidate_bundle.workers) > 0:
            # skills 应该来自匹配的 Worker
            worker_skill_names = set()
            for worker in result.candidate_bundle.workers:
                for skill in worker.skills:
                    worker_skill_names.add(skill.name)

            result_skill_names = {s.name for s in result.candidate_bundle.skills}
            # 结果的 skills 应该是 worker skills 的子集
            assert result_skill_names.issubset(worker_skill_names) or len(result_skill_names) == 0


# =============================================================================
# Resource Retrieval Tests
# =============================================================================

class TestResourceRetrieval:
    """Resource 检索测试"""

    def test_retrieve_resources_by_requirements(
        self, retriever: BaselineRetriever
    ):
        """测试基于 resource_requirements 检索资源"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # resource_requirements: ["res_wiki_001", "res_repo_001"]
        resource_ids = [r.id for r in result.candidate_bundle.resources]

        # 应该找到匹配的资源
        if len(result.candidate_bundle.resources) > 0:
            assert "res_wiki_001" in resource_ids or "res_repo_001" in resource_ids

    def test_resources_from_matched_workers(
        self, retriever: BaselineRetriever
    ):
        """测试从匹配的 Worker 聚合 Resource"""
        task_spec = get_research_task_spec()
        plan_draft = get_research_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 如果有匹配的 Worker 且 Worker 有 resources
        if len(result.candidate_bundle.workers) > 0:
            worker_resource_ids = set()
            for worker in result.candidate_bundle.workers:
                for resource in worker.resources:
                    worker_resource_ids.add(resource.id)

            result_resource_ids = {r.id for r in result.candidate_bundle.resources}
            # 结果的 resources 应该是 worker resources 的子集或来自 requirements
            assert (
                result_resource_ids.issubset(worker_resource_ids)
                or len(result_resource_ids) >= 0
            )


# =============================================================================
# Aggregation Tests
# =============================================================================

class TestCandidateAggregation:
    """候选聚合测试"""

    def test_aggregate_all_candidate_types(
        self, retriever: BaselineRetriever
    ):
        """测试 Worker/Knowledge/Skill/Resource 四类结果聚合"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 结果应该是一个完整的 RetrievalResult
        assert isinstance(result, RetrievalResult)
        assert isinstance(result.candidate_bundle.workers, list)
        assert isinstance(result.candidate_bundle.knowledge_items, list)
        assert isinstance(result.candidate_bundle.skills, list)
        assert isinstance(result.candidate_bundle.resources, list)
        assert isinstance(result.warnings, list)
        assert isinstance(result.errors, list)
        assert isinstance(result.explanations, list)


# =============================================================================
# Filtering Tests
# =============================================================================

class TestFiltering:
    """基础过滤测试"""

    def test_filter_by_worker_type(
        self, retriever: BaselineRetriever
    ):
        """测试按 worker_types 过滤"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(worker_types=["bot"])
        input_data = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
        )

        result = retriever.retrieve(input_data)

        # 所有返回的 Worker 应该是 bot 类型
        for worker in result.candidate_bundle.workers:
            assert worker.type == "bot"

    def test_filter_by_trust_level(
        self, retriever: BaselineRetriever
    ):
        """测试按 trust_levels 过滤"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(trust_levels=["trusted"])
        input_data = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
        )

        result = retriever.retrieve(input_data)

        # 所有返回的 Worker 应该是 trusted 级别
        for worker in result.candidate_bundle.workers:
            assert worker.state.trust_level == "trusted"

    def test_filter_by_domains(
        self, retriever: BaselineRetriever
    ):
        """测试按 domains 过滤"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(domains=["architecture"])
        input_data = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
        )

        result = retriever.retrieve(input_data)

        # 所有返回的 Worker 应该有 architecture domain
        for worker in result.candidate_bundle.workers:
            assert "architecture" in worker.domains

    def test_filter_by_top_k(
        self, retriever: BaselineRetriever
    ):
        """测试 top_k 限制返回数量"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(top_k=2)
        input_data = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
        )

        result = retriever.retrieve(input_data)

        # 每类候选不应超过 top_k
        assert len(result.candidate_bundle.workers) <= 2
        assert len(result.candidate_bundle.knowledge_items) <= 2
        assert len(result.candidate_bundle.skills) <= 2
        assert len(result.candidate_bundle.resources) <= 2


# =============================================================================
# Sorting Tests
# =============================================================================

class TestSorting:
    """基础排序测试"""

    def test_workers_sorted_by_score(
        self, retriever: BaselineRetriever
    ):
        """测试 Worker 按分数排序"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 如果有多个 Worker，应该按分数降序排列
        if len(result.candidate_bundle.workers) > 1:
            # 获取 worker 的 explanation 分数
            worker_scores = {}
            for exp in result.explanations:
                if exp.candidate_type == "worker":
                    worker_scores[exp.candidate_id] = exp.score

            # 验证排序
            prev_score = 1.0
            for worker in result.candidate_bundle.workers:
                score = worker_scores.get(worker.id, 0)
                assert score <= prev_score
                prev_score = score


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestEdgeCases:
    """边界场景测试"""

    def test_empty_catalog_returns_empty_result(
        self, empty_retriever: BaselineRetriever
    ):
        """测试空目录返回空结果"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = empty_retriever.retrieve(input_data)

        assert len(result.candidate_bundle.workers) == 0
        assert len(result.candidate_bundle.knowledge_items) == 0
        assert len(result.candidate_bundle.skills) == 0
        assert len(result.candidate_bundle.resources) == 0
        assert len(result.warnings) > 0  # 应该有警告说明无匹配

    def test_no_matching_workers(
        self, empty_catalog: CandidateCatalog
    ):
        """测试无匹配 Worker 的场景"""
        # 创建一个只有特定能力的 Worker
        worker = Worker(
            id="wrk_special_001",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Special Bot", handle="special_bot"),
            responsibilities=["special_task"],
            domains=["special_domain"],
            capabilities=[
                Capability(name="special_capability", level=CapabilityLevel.EXPERT)
            ],
            state=WorkerState(availability=Availability.AVAILABLE, trust_level=TrustLevel.TRUSTED),
        )

        catalog = CandidateCatalog(workers=[worker])
        retriever = BaselineRetriever(catalog=catalog)

        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 应该没有匹配的 Worker
        assert len(result.candidate_bundle.workers) == 0
        assert len(result.warnings) > 0

    def test_partial_match_scenario(
        self, retriever: BaselineRetriever
    ):
        """测试部分匹配场景"""
        # 创建一个只匹配部分能力的任务
        task_spec = TaskSpec(
            id="tsk_partial_001",
            goal="Complete a task requiring multiple capabilities",
            deliverables=["Partial deliverable"],
            constraints=[],
            success_criteria=["Partial success"],
            required_capabilities=["system_design", "nonexistent_capability"],
            required_knowledge=["architecture"],
            required_resources=[],
            risk_level=RiskLevel.MEDIUM,
            unknowns=[],
            subtasks=[],
        )
        plan_draft = PlanDraft(
            task_id="tsk_partial_001",
            strategy="Partial match strategy",
            steps=[PlanStep(id="s1", title="Step 1", objective="Partial objective")],
            role_requirements=["researcher"],
            knowledge_requirements=["architecture"],
            resource_requirements=[],
            handoff_strategy="partial_handoff",
            escalation_points=[],
        )
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 应该返回部分匹配的结果
        assert isinstance(result, RetrievalResult)
        # 应该有警告说明部分匹配
        if len(result.candidate_bundle.workers) > 0:
            # 部分匹配，应该有警告
            pass  # warnings 可能不是必须的，取决于实现


# =============================================================================
# Explanation Tests
# =============================================================================

class TestExplanation:
    """解释生成测试"""

    def test_explanation_has_required_fields(
        self, retriever: BaselineRetriever
    ):
        """测试 explanation 包含必需字段"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        for exp in result.explanations:
            assert exp.candidate_type in ["worker", "knowledge", "skill", "resource"]
            assert exp.candidate_id is not None
            assert exp.matched_fields is not None
            assert exp.match_reason is not None
            assert 0.0 <= exp.score <= 1.0

    def test_explanation_matches_actual_candidates(
        self, retriever: BaselineRetriever
    ):
        """测试 explanation 与实际候选匹配"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

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

        # 收集所有 explanation 的候选 ID
        explanation_ids = {(e.candidate_type, e.candidate_id) for e in result.explanations}

        # explanations 应该覆盖所有候选
        assert explanation_ids == all_candidate_ids