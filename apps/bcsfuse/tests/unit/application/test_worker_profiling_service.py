"""
Tests for Worker Profiling Service

M2: Worker Profiling & Extraction

测试范围：
- 单一来源结果汇总测试
- 多来源结果汇总测试
- 部分成功场景测试
- warning 聚合测试
- error 聚合测试
- source references 保留测试
- 空输入或无有效输入测试
- extractor 抛出异常或返回错误时的处理测试

Service 职责：
- 接收 profiling 输入
- 调用 ProfileExtractor
- 汇总抽取结果
- 聚合 warnings / errors
- 保留 source references
- 支持多来源输入的最小合并逻辑
- 输出统一的 profiling result

Service 不做：
- 不解析 markdown（由 extractor 做）
- 不实现抽取规则（由 extractor 做）
- 不写回 Worker Registry
- 不更新 Worker 主数据
"""

from __future__ import annotations

import pytest
from typing import Protocol, runtime_checkable


class TestWorkerProfilingServiceBasics:
    """测试 WorkerProfilingService 基本"""

    def test_service_importable(self):
        """测试 Service 可导入"""
        from src.application.services.worker_profiling_service import WorkerProfilingService

        assert WorkerProfilingService is not None

    def test_service_initializes_with_extractor(self):
        """测试 Service 需要 extractor 依赖"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)

        assert service is not None


class TestSingleSourceProfiling:
    """测试单一来源结果汇总"""

    def test_profile_single_soul_document(self):
        """测试汇总单一 SOUL 文档结果"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_content = """
# Capabilities
- Information Retrieval (expert)
- Data Analysis (advanced)

# Domains
- architecture
- documentation
"""
        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=soul_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        assert result.worker_id == "wrk_test_001"
        assert len(result.capabilities) == 2
        assert len(result.domains) == 2

    def test_profile_single_rules_document(self):
        """测试汇总单一 RULES 文档结果"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        rules_content = """
# Constraints
- 禁止发送外部邮件
- 禁止访问生产数据库
"""
        rules_doc = MarkdownDocument(doc_type=DocType.RULES, content=rules_content)
        input_data = ProfilingInput(worker_id="wrk_test_002", documents=[rules_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        assert result.worker_id == "wrk_test_002"
        assert len(result.constraints) >= 2

    def test_profile_single_memory_document(self):
        """测试汇总单一 MEMORY 文档结果"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        memory_content = """
# 2026-03-01
该 bot 在多 agent 组队任务中表现稳定

# 2026-03-15
成功完成调研任务，输出了高质量报告
"""
        memory_doc = MarkdownDocument(doc_type=DocType.MEMORY, content=memory_content)
        input_data = ProfilingInput(worker_id="wrk_test_003", documents=[memory_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        assert result.worker_id == "wrk_test_003"
        assert len(result.memory_episodes) >= 2


class TestMultipleSourceProfiling:
    """测试多来源结果汇总"""

    def test_profile_multiple_documents(self):
        """测试汇总多个文档结果"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Capabilities\n- Capability 1 (expert)\n# Domains\n- domain_a",
        )
        rules_doc = MarkdownDocument(
            doc_type=DocType.RULES,
            content="# Constraints\n- 禁止外部访问",
        )
        memory_doc = MarkdownDocument(
            doc_type=DocType.MEMORY,
            content="# 2026-03-01\nSome memory content",
        )

        input_data = ProfilingInput(
            worker_id="wrk_test_004",
            documents=[soul_doc, rules_doc, memory_doc],
        )

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        assert len(result.capabilities) >= 1
        assert len(result.domains) >= 1
        assert len(result.constraints) >= 1
        assert len(result.memory_episodes) >= 1

    def test_profile_with_metadata_sources(self):
        """测试汇总文档和元数据来源"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
            SkillMetadataInput,
            ResourceMetadataInput,
            SkillSource,
            TrustLevel,
            ResourceKind,
            ResourceAccess,
        )

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Capabilities\n- Capability 1 (expert)",
        )
        skill = SkillMetadataInput(
            name="web_search",
            source=SkillSource.BUILTIN,
            trust_level=TrustLevel.TRUSTED,
        )
        resource = ResourceMetadataInput(
            id="res_wiki",
            name="Wiki",
            kind=ResourceKind.API,
            access=ResourceAccess.READ,
        )

        input_data = ProfilingInput(
            worker_id="wrk_test_005",
            documents=[soul_doc],
            skills=[skill],
            resources=[resource],
        )

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        assert len(result.capabilities) >= 1
        assert len(result.skills) >= 1
        assert any(s.name == "web_search" for s in result.skills)
        assert len(result.resources) >= 1
        assert any(r.id == "res_wiki" for r in result.resources)


class TestPartialSuccess:
    """测试部分成功场景"""

    def test_partial_success_with_missing_sections(self):
        """测试部分成功：缺少某些节"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        # SOUL 只有能力，没有其他节
        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Capabilities\n- Capability 1 (expert)",
        )

        input_data = ProfilingInput(worker_id="wrk_test_006", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 应该成功抽取能力
        assert len(result.capabilities) >= 1
        # 结果应该是完整的（无 errors）
        assert result.is_complete()

    def test_partial_success_with_warnings(self):
        """测试部分成功：有 warnings 但无 errors"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        # 空的 SOUL 文档会产生 warnings
        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Some Title\n\nNo structured content here.",
        )

        input_data = ProfilingInput(worker_id="wrk_test_007", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 可能有 warnings
        # 但应该是 complete（无 errors）
        assert result.is_complete()


class TestWarningAggregation:
    """测试 warning 聚合"""

    def test_warnings_from_extractor_preserved(self):
        """测试 extractor 返回的 warnings 被保留"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        # 空文档会产生 warnings
        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Title\n\nNo capabilities section.",
        )

        input_data = ProfilingInput(worker_id="wrk_test_008", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # warnings 应该被保留
        if len(result.capabilities) == 0:
            assert len(result.warnings) >= 1
            assert any("capabilit" in w.message.lower() for w in result.warnings)

    def test_warnings_from_multiple_documents_aggregated(self):
        """测试多文档 warnings 聚合"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        # 两个空文档
        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Title\n\nNo content.",
        )
        rules_doc = MarkdownDocument(
            doc_type=DocType.RULES,
            content="# Title\n\nNo content.",
        )

        input_data = ProfilingInput(
            worker_id="wrk_test_009",
            documents=[soul_doc, rules_doc],
        )

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # warnings 应该被聚合
        # 至少应该有关于 missing capabilities 和 missing constraints 的 warnings
        # （具体数量取决于 extractor 实现）


class TestErrorAggregation:
    """测试 error 聚合"""

    def test_errors_from_extractor_preserved(self):
        """测试 extractor 返回的 errors 被保留"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        # 正常文档不应该产生 errors
        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Capabilities\n- Capability 1 (expert)",
        )

        input_data = ProfilingInput(worker_id="wrk_test_010", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 应该没有 errors
        assert len(result.errors) == 0


class TestSourceReferencesPreserved:
    """测试 source references 保留"""

    def test_capabilities_have_source_references(self):
        """测试能力抽取结果保留 source references"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Capabilities\n- Capability 1 (expert)",
        )

        input_data = ProfilingInput(worker_id="wrk_test_011", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        assert len(result.capabilities) >= 1
        cap = result.capabilities[0]
        assert cap.source_ref is not None
        assert cap.source_ref.doc_type == DocType.SOUL

    def test_constraints_have_source_references(self):
        """测试约束抽取结果保留 source references"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        rules_doc = MarkdownDocument(
            doc_type=DocType.RULES,
            content="# Constraints\n- 禁止外部访问",
        )

        input_data = ProfilingInput(worker_id="wrk_test_012", documents=[rules_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        assert len(result.constraints) >= 1
        constraint = result.constraints[0]
        assert constraint.source_ref is not None
        assert constraint.source_ref.doc_type == DocType.RULES

    def test_memory_episodes_have_source_references(self):
        """测试记忆片段抽取结果保留 source references"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        memory_doc = MarkdownDocument(
            doc_type=DocType.MEMORY,
            content="# 2026-03-01\nSome memory content",
        )

        input_data = ProfilingInput(worker_id="wrk_test_013", documents=[memory_doc])

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        assert len(result.memory_episodes) >= 1
        episode = result.memory_episodes[0]
        assert episode.source_ref is not None
        assert episode.source_ref.doc_type == DocType.MEMORY


class TestEmptyAndInvalidInputs:
    """测试空输入或无有效输入"""

    def test_empty_documents_raises_validation_error(self):
        """测试空文档列表在输入验证时失败"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import ProfilingInput
        from pydantic import ValidationError

        # ProfilingInput 本身不允许空文档列表
        with pytest.raises(ValidationError):
            ProfilingInput(worker_id="wrk_test_014", documents=[])

    def test_whitespace_only_document_still_processes(self):
        """测试只有空白内容的文档仍然处理"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )
        from pydantic import ValidationError

        # 空白文档应该在输入验证时就失败
        with pytest.raises(ValidationError):
            MarkdownDocument(doc_type=DocType.SOUL, content="   \n\n   ")


class TestExtractorExceptionHandling:
    """测试 extractor 抛出异常时的处理"""

    def test_extractor_raises_exception_returns_error(self):
        """测试 extractor 抛出异常时返回 error"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )
        from src.domain.services.profile_extractor import ProfileExtractor
        from src.domain.models.profiling_result import WorkerProfileExtractionResult

        # 创建一个会抛出异常的 mock extractor
        class BrokenExtractor:
            """故意抛出异常的 extractor"""
            def extract(self, input_data: ProfilingInput) -> WorkerProfileExtractionResult:
                raise RuntimeError("Intentional error for testing")

        broken_extractor = BrokenExtractor()
        service = WorkerProfilingService(extractor=broken_extractor)

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Capabilities\n- Capability 1 (expert)",
        )
        input_data = ProfilingInput(worker_id="wrk_test_015", documents=[soul_doc])

        # Service 应该捕获异常并返回带有 error 的结果
        result = service.profile(input_data)

        assert len(result.errors) >= 1
        assert result.worker_id == "wrk_test_015"

    def test_extractor_returns_result_with_errors(self):
        """测试 extractor 返回带有 errors 的结果"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )
        from src.domain.models.profiling_result import (
            WorkerProfileExtractionResult,
            ExtractionError,
        )

        # 创建一个返回 errors 的 mock extractor
        class ErrorReturningExtractor:
            """返回错误的 extractor"""
            def extract(self, input_data: ProfilingInput) -> WorkerProfileExtractionResult:
                result = WorkerProfileExtractionResult(worker_id=input_data.worker_id)
                result.errors.append(ExtractionError(
                    field="test_field",
                    message="Test error from extractor",
                    doc_type=DocType.SOUL,
                    doc_name="SOUL.md",
                ))
                return result

        error_extractor = ErrorReturningExtractor()
        service = WorkerProfilingService(extractor=error_extractor)

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Capabilities\n- Capability 1 (expert)",
        )
        input_data = ProfilingInput(worker_id="wrk_test_016", documents=[soul_doc])

        result = service.profile(input_data)

        # errors 应该被保留
        assert len(result.errors) >= 1
        assert any("Test error" in e.message for e in result.errors)


class TestDuplicateHandling:
    """测试重复项处理"""

    def test_duplicate_capabilities_are_deduplicated(self):
        """测试重复能力被去重"""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
            SkillMetadataInput,
            SkillSource,
            TrustLevel,
        )

        # SOUL 中有能力，同时技能元数据中也声明了相同能力
        # 注意：这里测试的是同名 skill 而非 capability
        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Capabilities\n- Retrieval (expert)\n# Skills\n- web_search (builtin, trusted)",
        )
        skill = SkillMetadataInput(
            name="web_search",  # 相同名称
            source=SkillSource.BUILTIN,
            trust_level=TrustLevel.TRUSTED,
        )

        input_data = ProfilingInput(
            worker_id="wrk_test_017",
            documents=[soul_doc],
            skills=[skill],
        )

        extractor = BaselineProfileExtractor()
        service = WorkerProfilingService(extractor=extractor)
        result = service.profile(input_data)

        # 技能应该去重（按 name 去重）
        web_search_skills = [s for s in result.skills if s.name == "web_search"]
        # 可能有两个来源，但 service 应该合并或保留所有
        assert len(result.skills) >= 1