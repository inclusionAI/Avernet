"""
Tests for RetrievalInput Domain Model

M5: Unified Retrieval Fabric

测试 RetrievalInput 模型的构造、字段校验和关联关系。
"""

from __future__ import annotations

import pytest

from src.domain.models.retrieval_input import (
    RetrievalInput,
    RetrievalFilters,
    RetrievalHints,
)
from src.domain.models.task_spec import TaskSpec, RiskLevel, Subtask
from src.domain.models.plan_draft import PlanDraft, PlanStep
from tests.fixtures.retrieval_data import (
    get_architecture_design_task_spec,
    get_architecture_plan_draft,
)


# =============================================================================
# RetrievalFilters Tests
# =============================================================================

class TestRetrievalFilters:
    """RetrievalFilters 测试"""

    def test_create_empty_filters(self):
        """测试创建空过滤器"""
        filters = RetrievalFilters()
        assert filters.worker_types is None
        assert filters.domains is None
        assert filters.trust_levels is None
        assert filters.top_k is None

    def test_create_filters_with_worker_types(self):
        """测试创建带 worker_types 的过滤器"""
        filters = RetrievalFilters(worker_types=["bot"])
        assert filters.worker_types == ["bot"]

    def test_create_filters_with_multiple_worker_types(self):
        """测试创建带多个 worker_types 的过滤器"""
        filters = RetrievalFilters(worker_types=["bot", "human"])
        assert filters.worker_types == ["bot", "human"]

    def test_create_filters_with_domains(self):
        """测试创建带 domains 的过滤器"""
        filters = RetrievalFilters(domains=["architecture", "research"])
        assert filters.domains == ["architecture", "research"]

    def test_create_filters_with_trust_levels(self):
        """测试创建带 trust_levels 的过滤器"""
        filters = RetrievalFilters(trust_levels=["trusted", "guarded"])
        assert filters.trust_levels == ["trusted", "guarded"]

    def test_create_filters_with_top_k(self):
        """测试创建带 top_k 的过滤器"""
        filters = RetrievalFilters(top_k=20)
        assert filters.top_k == 20

    def test_create_complete_filters(self):
        """测试创建完整过滤器"""
        filters = RetrievalFilters(
            worker_types=["bot"],
            domains=["architecture"],
            trust_levels=["trusted"],
            top_k=10,
        )
        assert filters.worker_types == ["bot"]
        assert filters.domains == ["architecture"]
        assert filters.trust_levels == ["trusted"]
        assert filters.top_k == 10


# =============================================================================
# RetrievalHints Tests
# =============================================================================

class TestRetrievalHints:
    """RetrievalHints 测试"""

    def test_create_empty_hints(self):
        """测试创建空提示"""
        hints = RetrievalHints()
        assert hints.preferred_worker_ids is None
        assert hints.preferred_skill_names is None
        assert hints.preferred_resource_ids is None
        assert hints.excluded_worker_ids is None

    def test_create_hints_with_preferred_workers(self):
        """测试创建带 preferred_worker_ids 的提示"""
        hints = RetrievalHints(preferred_worker_ids=["wrk_architect_001"])
        assert hints.preferred_worker_ids == ["wrk_architect_001"]

    def test_create_hints_with_preferred_skills(self):
        """测试创建带 preferred_skill_names 的提示"""
        hints = RetrievalHints(preferred_skill_names=["web_search", "code_generator"])
        assert hints.preferred_skill_names == ["web_search", "code_generator"]

    def test_create_hints_with_preferred_resources(self):
        """测试创建带 preferred_resource_ids 的提示"""
        hints = RetrievalHints(preferred_resource_ids=["res_wiki_001"])
        assert hints.preferred_resource_ids == ["res_wiki_001"]

    def test_create_hints_with_excluded_workers(self):
        """测试创建带 excluded_worker_ids 的提示"""
        hints = RetrievalHints(excluded_worker_ids=["wrk_busy_001"])
        assert hints.excluded_worker_ids == ["wrk_busy_001"]

    def test_create_complete_hints(self):
        """测试创建完整提示"""
        hints = RetrievalHints(
            preferred_worker_ids=["wrk_architect_001"],
            preferred_skill_names=["code_generator"],
            preferred_resource_ids=["res_repo_001"],
            excluded_worker_ids=["wrk_busy_001"],
        )
        assert hints.preferred_worker_ids == ["wrk_architect_001"]
        assert hints.preferred_skill_names == ["code_generator"]
        assert hints.preferred_resource_ids == ["res_repo_001"]
        assert hints.excluded_worker_ids == ["wrk_busy_001"]


# =============================================================================
# RetrievalInput Tests
# =============================================================================

class TestRetrievalInput:
    """RetrievalInput 测试"""

    def test_create_with_required_fields_only(self):
        """测试仅使用必填字段创建"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()

        retrieval_input = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
        )

        assert retrieval_input.task_spec.id == "tsk_arch_design_001"
        assert retrieval_input.plan_draft.task_id == "tsk_arch_design_001"
        assert retrieval_input.filters is None
        assert retrieval_input.hints is None

    def test_create_with_filters(self):
        """测试带过滤器创建"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(worker_types=["bot"], top_k=10)

        retrieval_input = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
        )

        assert retrieval_input.filters.worker_types == ["bot"]
        assert retrieval_input.filters.top_k == 10

    def test_create_with_hints(self):
        """测试带提示创建"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        hints = RetrievalHints(preferred_worker_ids=["wrk_architect_001"])

        retrieval_input = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            hints=hints,
        )

        assert retrieval_input.hints.preferred_worker_ids == ["wrk_architect_001"]

    def test_create_with_filters_and_hints(self):
        """测试带过滤器和提示创建"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(worker_types=["bot"], domains=["architecture"])
        hints = RetrievalHints(preferred_worker_ids=["wrk_architect_001"])

        retrieval_input = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
            hints=hints,
        )

        assert retrieval_input.filters.worker_types == ["bot"]
        assert retrieval_input.hints.preferred_worker_ids == ["wrk_architect_001"]

    def test_task_spec_and_plan_draft_alignment(self):
        """测试 task_spec 和 plan_draft 的关联"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()

        retrieval_input = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
        )

        # plan_draft.task_id 应该与 task_spec.id 对齐
        assert retrieval_input.plan_draft.task_id == retrieval_input.task_spec.id

    def test_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()

        with pytest.raises(Exception):  # ValidationError
            RetrievalInput(
                task_spec=task_spec,
                plan_draft=plan_draft,
                unknown_field="invalid",  # type: ignore
            )


# =============================================================================
# Integration Tests
# =============================================================================

class TestRetrievalInputIntegration:
    """RetrievalInput 集成测试"""

    def test_retrieval_input_provides_search_criteria(self):
        """测试 RetrievalInput 提供检索条件"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()

        retrieval_input = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
        )

        # 从 task_spec 获取能力需求
        assert "system_design" in retrieval_input.task_spec.required_capabilities
        assert "documentation" in retrieval_input.task_spec.required_capabilities

        # 从 plan_draft 获取角色需求
        assert "researcher" in retrieval_input.plan_draft.role_requirements
        assert "architect" in retrieval_input.plan_draft.role_requirements

        # 从 plan_draft 获取知识需求
        assert "architecture" in retrieval_input.plan_draft.knowledge_requirements

        # 从 plan_draft 获取资源需求
        assert "res_wiki_001" in retrieval_input.plan_draft.resource_requirements

    def test_filters_constrain_search(self):
        """测试过滤器约束检索"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        filters = RetrievalFilters(
            worker_types=["bot"],
            domains=["architecture"],
            trust_levels=["trusted"],
            top_k=5,
        )

        retrieval_input = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            filters=filters,
        )

        # 过滤条件可用于约束检索结果
        assert retrieval_input.filters.worker_types == ["bot"]
        assert retrieval_input.filters.domains == ["architecture"]
        assert retrieval_input.filters.trust_levels == ["trusted"]
        assert retrieval_input.filters.top_k == 5

    def test_hints_guide_search(self):
        """测试提示引导检索"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()
        hints = RetrievalHints(
            preferred_worker_ids=["wrk_architect_001"],
            excluded_worker_ids=["wrk_busy_001"],
        )

        retrieval_input = RetrievalInput(
            task_spec=task_spec,
            plan_draft=plan_draft,
            hints=hints,
        )

        # 提示可用于引导检索结果
        assert "wrk_architect_001" in (retrieval_input.hints.preferred_worker_ids or [])
        assert "wrk_busy_001" in (retrieval_input.hints.excluded_worker_ids or [])