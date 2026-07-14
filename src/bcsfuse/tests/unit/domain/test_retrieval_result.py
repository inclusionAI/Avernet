"""
Tests for RetrievalResult Domain Model

M5: Unified Retrieval Fabric

测试 RetrievalResult 模型的构造、字段校验和行为。

RetrievalResult 承载检索的完整输出，包括：
- candidate_bundle: 核心候选集（与 schema 对齐）
- warnings: 检索过程中的警告
- errors: 检索过程中的错误
- explanations: 结果解释
"""

from __future__ import annotations

import pytest

from src.domain.models.retrieval_result import (
    RetrievalResult,
    RetrievalExplanation,
)
from src.domain.models.candidate_bundle import CandidateBundle, KnowledgeItem
from src.domain.models.worker import Worker, WorkerType, WorkerIdentity, Capability, CapabilityLevel, WorkerState, Availability, TrustLevel
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from tests.fixtures.retrieval_data import (
    get_all_workers,
    get_all_knowledge_items,
    get_architecture_design_task_spec,
    get_architecture_plan_draft,
)


# =============================================================================
# RetrievalExplanation Tests
# =============================================================================

class TestRetrievalExplanation:
    """RetrievalExplanation 测试"""

    def test_create_explanation_for_worker(self):
        """测试为 Worker 创建解释"""
        explanation = RetrievalExplanation(
            candidate_type="worker",
            candidate_id="wrk_architect_001",
            matched_fields=["capabilities.system_design", "domains.architecture"],
            match_reason="Worker has system_design capability at expert level and architecture domain",
            score=0.92,
        )

        assert explanation.candidate_type == "worker"
        assert explanation.candidate_id == "wrk_architect_001"
        assert "capabilities.system_design" in explanation.matched_fields
        assert explanation.score == 0.92

    def test_create_explanation_for_knowledge(self):
        """测试为 KnowledgeItem 创建解释"""
        explanation = RetrievalExplanation(
            candidate_type="knowledge",
            candidate_id="kno_arch_doc_001",
            matched_fields=["tags.architecture", "tags.design"],
            match_reason="Knowledge item tagged with architecture and design",
            score=0.85,
        )

        assert explanation.candidate_type == "knowledge"
        assert explanation.candidate_id == "kno_arch_doc_001"
        assert explanation.score == 0.85

    def test_create_explanation_for_skill(self):
        """测试为 Skill 创建解释"""
        explanation = RetrievalExplanation(
            candidate_type="skill",
            candidate_id="web_search",
            matched_fields=["name"],
            match_reason="Skill name matches requirement",
            score=1.0,
        )

        assert explanation.candidate_type == "skill"

    def test_create_explanation_for_resource(self):
        """测试为 Resource 创建解释"""
        explanation = RetrievalExplanation(
            candidate_type="resource",
            candidate_id="res_wiki_001",
            matched_fields=["id", "tags.documentation"],
            match_reason="Resource ID matches requirement and has documentation tag",
            score=0.95,
        )

        assert explanation.candidate_type == "resource"

    def test_explanation_with_low_score(self):
        """测试低分解释"""
        explanation = RetrievalExplanation(
            candidate_type="worker",
            candidate_id="wrk_partial_001",
            matched_fields=["domains.architecture"],
            match_reason="Partial match: only domain matches, capabilities not satisfied",
            score=0.45,
        )

        assert explanation.score == 0.45
        assert "Partial match" in explanation.match_reason


# =============================================================================
# RetrievalResult Tests
# =============================================================================

class TestRetrievalResult:
    """RetrievalResult 测试"""

    def test_create_with_candidate_bundle_only(self):
        """测试仅使用 candidate_bundle 创建"""
        bundle = CandidateBundle()
        result = RetrievalResult(candidate_bundle=bundle)

        assert result.candidate_bundle == bundle
        assert result.warnings == []
        assert result.errors == []
        assert result.explanations == []

    def test_create_with_candidates(self):
        """测试带候选创建"""
        workers = get_all_workers()[:2]
        knowledge_items = get_all_knowledge_items()[:2]
        bundle = CandidateBundle(workers=workers, knowledge_items=knowledge_items)

        result = RetrievalResult(candidate_bundle=bundle)

        assert len(result.candidate_bundle.workers) == 2
        assert len(result.candidate_bundle.knowledge_items) == 2

    def test_create_with_warnings(self):
        """测试带警告创建"""
        bundle = CandidateBundle()
        warnings = ["Some workers were filtered out due to trust level"]

        result = RetrievalResult(candidate_bundle=bundle, warnings=warnings)

        assert len(result.warnings) == 1
        assert "trust level" in result.warnings[0]

    def test_create_with_errors(self):
        """测试带错误创建"""
        bundle = CandidateBundle()
        errors = ["Failed to retrieve knowledge items: index unavailable"]

        result = RetrievalResult(candidate_bundle=bundle, errors=errors)

        assert len(result.errors) == 1
        assert "index unavailable" in result.errors[0]

    def test_create_with_explanations(self):
        """测试带解释创建"""
        bundle = CandidateBundle(workers=[get_all_workers()[0]])
        explanations = [
            RetrievalExplanation(
                candidate_type="worker",
                candidate_id="wrk_researcher_001",
                matched_fields=["capabilities.information_retrieval"],
                match_reason="Worker has information_retrieval capability",
                score=0.95,
            )
        ]

        result = RetrievalResult(candidate_bundle=bundle, explanations=explanations)

        assert len(result.explanations) == 1
        assert result.explanations[0].candidate_id == "wrk_researcher_001"

    def test_create_complete_result(self):
        """测试创建完整结果"""
        workers = get_all_workers()[:2]
        bundle = CandidateBundle(workers=workers)
        warnings = ["Partial results due to timeout"]
        errors = []
        explanations = [
            RetrievalExplanation(
                candidate_type="worker",
                candidate_id="wrk_architect_001",
                matched_fields=["capabilities.system_design"],
                match_reason="Capability match",
                score=0.92,
            )
        ]

        result = RetrievalResult(
            candidate_bundle=bundle,
            warnings=warnings,
            errors=errors,
            explanations=explanations,
        )

        assert len(result.candidate_bundle.workers) == 2
        assert len(result.warnings) == 1
        assert len(result.errors) == 0
        assert len(result.explanations) == 1

    def test_empty_result_scenario(self):
        """测试空结果场景"""
        bundle = CandidateBundle()
        warnings = ["No workers matched the required capabilities"]
        errors = []

        result = RetrievalResult(candidate_bundle=bundle, warnings=warnings, errors=errors)

        assert len(result.candidate_bundle.workers) == 0
        assert len(result.warnings) == 1
        assert result.candidate_bundle.knowledge_items == []
        assert result.candidate_bundle.skills == []
        assert result.candidate_bundle.resources == []

    def test_partial_result_scenario(self):
        """测试部分结果场景"""
        workers = [get_all_workers()[0]]  # 只有 1 个 worker
        bundle = CandidateBundle(workers=workers)
        warnings = [
            "No knowledge items found for the requirements",
            "No skills matched the criteria",
            "No resources found",
        ]

        result = RetrievalResult(candidate_bundle=bundle, warnings=warnings)

        assert len(result.candidate_bundle.workers) == 1
        assert len(result.candidate_bundle.knowledge_items) == 0
        assert len(result.warnings) == 3

    def test_error_scenario(self):
        """测试错误场景"""
        bundle = CandidateBundle()
        errors = ["Worker repository unavailable", "Knowledge index timeout"]

        result = RetrievalResult(candidate_bundle=bundle, errors=errors)

        assert len(result.errors) == 2
        assert result.candidate_bundle == bundle

    def test_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        bundle = CandidateBundle()

        with pytest.raises(Exception):  # ValidationError
            RetrievalResult(
                candidate_bundle=bundle,
                unknown_field="invalid",  # type: ignore
            )


# =============================================================================
# CandidateBundle Alignment Tests
# =============================================================================

class TestCandidateBundleSchemaAlignment:
    """CandidateBundle 与 schema 一致性测试"""

    def test_candidate_bundle_has_required_fields(self):
        """测试 CandidateBundle 有必需字段"""
        bundle = CandidateBundle()

        # CandidateBundle 应该有这些字段（与 schema 对齐）
        assert hasattr(bundle, "workers")
        assert hasattr(bundle, "knowledge_items")
        assert hasattr(bundle, "skills")
        assert hasattr(bundle, "resources")
        assert hasattr(bundle, "evidence")

        # 不应该有 warnings/errors/explanations（这些在 RetrievalResult 中）
        assert not hasattr(bundle, "warnings")
        assert not hasattr(bundle, "errors")
        assert not hasattr(bundle, "explanations")

    def test_candidate_bundle_default_values(self):
        """测试 CandidateBundle 默认值"""
        bundle = CandidateBundle()

        assert bundle.workers == []
        assert bundle.knowledge_items == []
        assert bundle.skills == []
        assert bundle.resources == []
        assert bundle.evidence == []

    def test_candidate_bundle_with_data(self):
        """测试 CandidateBundle 数据填充"""
        workers = [get_all_workers()[0]]
        knowledge_items = [get_all_knowledge_items()[0]]
        bundle = CandidateBundle(
            workers=workers,
            knowledge_items=knowledge_items,
            evidence=["doc_ref_1", "doc_ref_2"],
        )

        assert len(bundle.workers) == 1
        assert len(bundle.knowledge_items) == 1
        assert len(bundle.evidence) == 2


# =============================================================================
# Integration Tests
# =============================================================================

class TestRetrievalResultIntegration:
    """RetrievalResult 集成测试"""

    def test_result_from_retrieval_input_context(self):
        """测试从 RetrievalInput 上下文创建结果"""
        task_spec = get_architecture_design_task_spec()
        plan_draft = get_architecture_plan_draft()

        # 模拟检索过程
        matched_workers = []
        explanations = []

        for worker in get_all_workers():
            for cap in worker.capabilities:
                if cap.name in task_spec.required_capabilities:
                    matched_workers.append(worker)
                    explanations.append(
                        RetrievalExplanation(
                            candidate_type="worker",
                            candidate_id=worker.id,
                            matched_fields=[f"capabilities.{cap.name}"],
                            match_reason=f"Worker has {cap.name} capability",
                            score=0.9,
                        )
                    )
                    break

        bundle = CandidateBundle(workers=matched_workers)
        result = RetrievalResult(candidate_bundle=bundle, explanations=explanations)

        # 验证结果
        assert len(result.candidate_bundle.workers) > 0
        assert len(result.explanations) > 0

    def test_result_summarizes_retrieval_process(self):
        """测试结果汇总检索过程"""
        bundle = CandidateBundle(
            workers=get_all_workers()[:3],
            knowledge_items=get_all_knowledge_items()[:2],
        )

        warnings = ["3 workers filtered out due to availability"]
        errors = []
        explanations = [
            RetrievalExplanation(
                candidate_type="worker",
                candidate_id="wrk_architect_001",
                matched_fields=["capabilities.system_design"],
                match_reason="Capability match",
                score=0.92,
            )
        ]

        result = RetrievalResult(
            candidate_bundle=bundle,
            warnings=warnings,
            errors=errors,
            explanations=explanations,
        )

        # Verification
        assert result.candidate_bundle is not None
        assert len(result.warnings) == 1
        assert len(result.errors) == 0
        assert len(result.explanations) == 1
        assert len(result.candidate_bundle.workers) == 3
        assert len(result.candidate_bundle.knowledge_items) == 2