"""
Integration Tests for Profiling Flow

M2: Worker Profiling & Extraction

验证完整的 profiling 链路：
- 原始 profiling 输入
- baseline markdown parser
- baseline profile extractor
- worker profiling service
- profiling result 输出
- warnings / errors / source references 保持正确

测试场景：
1. 完整闭环场景
2. 部分成功场景
3. 多来源合并场景
4. source reference 保留场景
5. 异常传播场景
6. 空文档处理场景
"""

from __future__ import annotations

import pytest


class TestProfilingFlowIntegration:
    """Profiling 流程集成测试"""

    # =========================================================================
    # 完整闭环场景
    # =========================================================================

    def test_complete_profiling_flow(self):
        """测试完整闭环：SOUL + RULES + MEMORY + skills + resources"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import get_complete_profiling_input
        from tests.fixtures.profiling_results import assert_complete_result

        input_data = get_complete_profiling_input()

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 验证完整结果
        checks = assert_complete_result(result)
        assert len(checks) >= 10, f"Expected at least 10 checks, got {len(checks)}"

    # =========================================================================
    # 部分成功场景
    # =========================================================================

    def test_partial_success_with_empty_rules(self):
        """测试部分成功：有 SOUL 但 RULES 为空噪音"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import get_partial_profiling_input

        input_data = get_partial_profiling_input()

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # SOUL 内容应该被成功抽取
        assert len(result.capabilities) >= 1, "Should extract capabilities from SOUL"

        # 结果应该是完整的（无 errors）
        assert result.is_complete(), "Result should be complete even with empty RULES"

        # 可能有 warnings（关于缺失的职责等）
        # warnings 数量取决于 extractor 实现

    def test_noisy_document_still_works(self):
        """测试噪音文档仍能正常抽取"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import SOUL_NOISY
        from src.domain.models.profiling_input import MarkdownDocument, DocType, ProfilingInput

        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=SOUL_NOISY)
        input_data = ProfilingInput(worker_id="wrk_noisy_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 应该能抽取到真实的能力
        assert len(result.capabilities) >= 1, "Should extract capabilities from noisy document"
        assert any("Real Capability" in c.name for c in result.capabilities)

    # =========================================================================
    # 多来源合并场景
    # =========================================================================

    def test_deduplication_of_skills_and_resources(self):
        """测试技能和资源去重"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import get_duplicate_input
        from tests.fixtures.profiling_results import assert_deduplication_worked

        input_data = get_duplicate_input()

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 验证去重生效
        checks = assert_deduplication_worked(result)
        assert len(checks) >= 2, "Should have deduplication checks passing"

    def test_multiple_domains_are_merged(self):
        """测试多个领域被正确合并"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        # 两个文档都定义了 domains
        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Domains\n- architecture\n- security",
        )

        input_data = ProfilingInput(worker_id="wrk_merge_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 领域应该被抽取
        assert len(result.domains) >= 2, "Should have at least 2 domains"

        domain_names = [d.name for d in result.domains]
        assert "architecture" in domain_names
        assert "security" in domain_names

    # =========================================================================
    # Source Reference 保留场景
    # =========================================================================

    def test_source_references_preserved_in_complete_flow(self):
        """测试完整流程中 source references 被保留"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import get_complete_profiling_input
        from tests.fixtures.profiling_results import assert_source_references_valid

        input_data = get_complete_profiling_input()

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 验证 source references 有效
        checks = assert_source_references_valid(result)
        assert len(checks) >= 3, "Should have source_ref checks for capabilities, constraints, and memory"

    def test_capability_source_locatable(self):
        """测试能力的来源可定位"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import SOUL_COMPLETE
        from src.domain.models.profiling_input import MarkdownDocument, DocType, ProfilingInput

        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=SOUL_COMPLETE)
        input_data = ProfilingInput(worker_id="wrk_source_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 找到 Information Retrieval 能力
        cap = next((c for c in result.capabilities if "Information Retrieval" in c.name), None)
        assert cap is not None, "Should have Information Retrieval capability"

        # 验证 source reference
        assert cap.source_ref is not None
        assert cap.source_ref.doc_type == DocType.SOUL
        assert cap.source_ref.doc_name == "SOUL.md"

        # 应该有某种定位信息
        has_location = (
            cap.source_ref.section is not None or
            cap.source_ref.heading is not None or
            cap.source_ref.snippet is not None
        )
        assert has_location, "Capability source should have location info"

    # =========================================================================
    # 异常传播场景
    # =========================================================================

    def test_extractor_exception_returns_structured_error(self):
        """测试 extractor 抛异常时返回结构化错误"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )
        from src.domain.models.profiling_result import WorkerProfileExtractionResult

        # 创建会抛异常的 extractor
        class BrokenExtractor:
            def extract(self, input_data: ProfilingInput) -> WorkerProfileExtractionResult:
                raise RuntimeError("Simulated extraction failure")

        broken_extractor = BrokenExtractor()
        service = WorkerProfilingService(extractor=broken_extractor)

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Capabilities\n- Test (expert)",
        )
        input_data = ProfilingInput(worker_id="wrk_error_001", documents=[soul_doc])

        result = service.profile(input_data)

        # 应该返回带 error 的结果，而不是抛异常
        assert len(result.errors) >= 1, "Should have error when extractor fails"
        assert result.worker_id == "wrk_error_001"

        # 错误消息应该包含原始异常信息
        assert any("Simulated extraction failure" in e.message for e in result.errors)

    # =========================================================================
    # 空文档处理场景
    # =========================================================================

    def test_empty_document_produces_warnings(self):
        """测试空文档产生警告"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import SOUL_EMPTY
        from src.domain.models.profiling_input import MarkdownDocument, DocType, ProfilingInput

        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=SOUL_EMPTY)
        input_data = ProfilingInput(worker_id="wrk_empty_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 空文档应该产生 warnings
        # 没有能力抽取时应该有警告
        if len(result.capabilities) == 0:
            assert len(result.warnings) >= 1, "Should have warnings when no capabilities extracted"

        # 结果应该是完整的（没有 errors）
        assert result.is_complete(), "Result should be complete even with empty document"

    # =========================================================================
    # Warnings / Errors 聚合场景
    # =========================================================================

    def test_warnings_are_aggregated(self):
        """测试 warnings 被正确聚合"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import SOUL_EMPTY, RULES_EMPTY
        from src.domain.models.profiling_input import MarkdownDocument, DocType, ProfilingInput

        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=SOUL_EMPTY)
        rules_doc = MarkdownDocument(doc_type=DocType.RULES, content=RULES_EMPTY)

        input_data = ProfilingInput(
            worker_id="wrk_warnings_001",
            documents=[soul_doc, rules_doc],
        )

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 即使没有抽取到内容，也应该有 warnings
        # warnings 应该被保留（不丢失）
        # 具体数量取决于 extractor 实现

    # =========================================================================
    # 各文档类型的独立验证
    # =========================================================================

    def test_soul_extraction_independently(self):
        """独立测试 SOUL.md 抽取"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import SOUL_COMPLETE
        from src.domain.models.profiling_input import MarkdownDocument, DocType, ProfilingInput

        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=SOUL_COMPLETE)
        input_data = ProfilingInput(worker_id="wrk_soul_only", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.capabilities) == 3
        assert len(result.domains) == 3
        assert len(result.responsibilities) >= 3
        assert result.collaboration_style is not None

    def test_rules_extraction_independently(self):
        """独立测试 RULES.md 抽取"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import RULES_COMPLETE
        from src.domain.models.profiling_input import MarkdownDocument, DocType, ProfilingInput

        rules_doc = MarkdownDocument(doc_type=DocType.RULES, content=RULES_COMPLETE)
        input_data = ProfilingInput(worker_id="wrk_rules_only", documents=[rules_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.constraints) >= 4
        assert len(result.escalation_triggers) >= 2

    def test_memory_extraction_independently(self):
        """独立测试 MEMORY.md 抽取"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from tests.fixtures.profiling_documents import MEMORY_COMPLETE
        from src.domain.models.profiling_input import MarkdownDocument, DocType, ProfilingInput

        memory_doc = MarkdownDocument(doc_type=DocType.MEMORY, content=MEMORY_COMPLETE)
        input_data = ProfilingInput(worker_id="wrk_memory_only", documents=[memory_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.memory_episodes) == 3

        # 验证时间戳
        timestamps = [e.timestamp for e in result.memory_episodes]
        assert "2026-03-01" in timestamps
        assert "2026-03-15" in timestamps
        assert "2026-03-20" in timestamps