"""
Tests for Profiling Input Domain Models

M2: Worker Profiling & Extraction

测试范围：
- MarkdownDocument: markdown 文档输入
- SkillMetadataInput: 技能元数据输入
- ResourceMetadataInput: 资源元数据输入
- ProfilingInput: 聚合输入
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestMarkdownDocument:
    """测试 MarkdownDocument 模型"""

    def test_create_soul_document_success(self):
        """测试创建 SOUL.md 文档"""
        from src.domain.models.profiling_input import MarkdownDocument, DocType

        content = """
# Identity
Name: Research Bot
Role: Researcher

# Capabilities
- Information Retrieval (expert)
- Data Analysis (advanced)
"""
        doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content=content,
            source_uri="file://workspace/SOUL.md",
        )

        assert doc.doc_type == DocType.SOUL
        assert doc.content == content
        assert doc.source_uri == "file://workspace/SOUL.md"
        assert doc.metadata == {}

    def test_create_rules_document_success(self):
        """测试创建 RULES.md 文档"""
        from src.domain.models.profiling_input import MarkdownDocument, DocType

        content = """
# Constraints
- 禁止发送外部邮件
- 写生产数据库前必须审批
"""
        doc = MarkdownDocument(
            doc_type=DocType.RULES,
            content=content,
        )

        assert doc.doc_type == DocType.RULES
        assert doc.content == content
        assert doc.source_uri is None

    def test_create_memory_document_success(self):
        """测试创建 MEMORY.md 文档"""
        from src.domain.models.profiling_input import MarkdownDocument, DocType

        content = """
# 2026-03-01
该 bot 在"多 agent 组队"任务中表现稳定

# 2026-03-15
成功完成了调研任务
"""
        doc = MarkdownDocument(
            doc_type=DocType.MEMORY,
            content=content,
            source_uri="file://workspace/MEMORY.md",
            metadata={"line_count": 10},
        )

        assert doc.doc_type == DocType.MEMORY
        assert doc.metadata["line_count"] == 10

    def test_create_document_with_metadata(self):
        """测试创建带元数据的文档"""
        from src.domain.models.profiling_input import MarkdownDocument, DocType

        doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Test",
            metadata={
                "author": "system",
                "version": "1.0",
            },
        )

        assert doc.metadata["author"] == "system"
        assert doc.metadata["version"] == "1.0"

    def test_empty_content_raises_error(self):
        """测试空内容抛出错误"""
        from src.domain.models.profiling_input import MarkdownDocument, DocType

        with pytest.raises(ValidationError) as exc_info:
            MarkdownDocument(
                doc_type=DocType.SOUL,
                content="",
            )

        assert "content" in str(exc_info.value).lower()

    def test_whitespace_only_content_raises_error(self):
        """测试仅含空白的文档抛出错误"""
        from src.domain.models.profiling_input import MarkdownDocument, DocType

        with pytest.raises(ValidationError) as exc_info:
            MarkdownDocument(
                doc_type=DocType.SOUL,
                content="   \n\t  ",
            )

        assert "content" in str(exc_info.value).lower()

    def test_invalid_doc_type_raises_error(self):
        """测试无效文档类型抛出错误"""
        from src.domain.models.profiling_input import MarkdownDocument

        with pytest.raises(ValidationError):
            MarkdownDocument(
                doc_type="invalid_type",  # type: ignore
                content="# Test",
            )


class TestSkillMetadataInput:
    """测试 SkillMetadataInput 模型"""

    def test_create_skill_metadata_success(self):
        """测试创建技能元数据"""
        from src.domain.models.profiling_input import (
            SkillMetadataInput,
            SkillSource,
            TrustLevel,
        )

        skill = SkillMetadataInput(
            name="web_search",
            source=SkillSource.BUILTIN,
            description="Search the web for information",
            trust_level=TrustLevel.TRUSTED,
            approval_required=False,
            tool_names=["search", "fetch"],
        )

        assert skill.name == "web_search"
        assert skill.source == SkillSource.BUILTIN
        assert skill.description == "Search the web for information"
        assert skill.trust_level == TrustLevel.TRUSTED
        assert skill.approval_required is False
        assert skill.tool_names == ["search", "fetch"]

    def test_create_skill_with_minimal_fields(self):
        """测试创建最小字段技能"""
        from src.domain.models.profiling_input import (
            SkillMetadataInput,
            SkillSource,
            TrustLevel,
        )

        skill = SkillMetadataInput(
            name="test_skill",
            source=SkillSource.PLUGIN,
            trust_level=TrustLevel.GUARDED,
        )

        assert skill.name == "test_skill"
        assert skill.description is None
        assert skill.approval_required is False  # 默认值
        assert skill.tool_names == []  # 默认值

    def test_skill_with_approval_required(self):
        """测试需要审批的技能"""
        from src.domain.models.profiling_input import (
            SkillMetadataInput,
            SkillSource,
            TrustLevel,
        )

        skill = SkillMetadataInput(
            name="send_email",
            source=SkillSource.MCP,
            trust_level=TrustLevel.SANDBOX_ONLY,
            approval_required=True,
        )

        assert skill.approval_required is True
        assert skill.trust_level == TrustLevel.SANDBOX_ONLY

    def test_skill_empty_name_raises_error(self):
        """测试空技能名抛出错误"""
        from src.domain.models.profiling_input import (
            SkillMetadataInput,
            SkillSource,
            TrustLevel,
        )

        with pytest.raises(ValidationError):
            SkillMetadataInput(
                name="",
                source=SkillSource.BUILTIN,
                trust_level=TrustLevel.TRUSTED,
            )


class TestResourceMetadataInput:
    """测试 ResourceMetadataInput 模型"""

    def test_create_resource_metadata_success(self):
        """测试创建资源元数据"""
        from src.domain.models.profiling_input import (
            ResourceMetadataInput,
            ResourceKind,
            ResourceAccess,
        )

        resource = ResourceMetadataInput(
            id="res_internal_wiki",
            name="Internal Wiki",
            kind=ResourceKind.API,
            description="Company internal wiki API",
            uri="https://wiki.internal",
            access=ResourceAccess.READ,
            owner="IT Team",
            tags=["documentation", "internal"],
        )

        assert resource.id == "res_internal_wiki"
        assert resource.name == "Internal Wiki"
        assert resource.kind == ResourceKind.API
        assert resource.access == ResourceAccess.READ
        assert "documentation" in resource.tags

    def test_create_resource_with_minimal_fields(self):
        """测试创建最小字段资源"""
        from src.domain.models.profiling_input import (
            ResourceMetadataInput,
            ResourceKind,
            ResourceAccess,
        )

        resource = ResourceMetadataInput(
            id="res_test_db",
            name="Test Database",
            kind=ResourceKind.DATASET,
            access=ResourceAccess.WRITE,
        )

        assert resource.id == "res_test_db"
        assert resource.description is None
        assert resource.uri is None
        assert resource.tags == []  # 默认值

    def test_resource_invalid_id_format_raises_error(self):
        """测试无效资源 ID 格式抛出错误"""
        from src.domain.models.profiling_input import (
            ResourceMetadataInput,
            ResourceKind,
            ResourceAccess,
        )

        with pytest.raises(ValidationError) as exc_info:
            ResourceMetadataInput(
                id="invalid_id",  # 缺少 res_ 前缀
                name="Test",
                kind=ResourceKind.FILE,
                access=ResourceAccess.READ,
            )

        assert "res_" in str(exc_info.value).lower() or "pattern" in str(exc_info.value).lower()

    def test_resource_empty_name_raises_error(self):
        """测试空资源名抛出错误"""
        from src.domain.models.profiling_input import (
            ResourceMetadataInput,
            ResourceKind,
            ResourceAccess,
        )

        with pytest.raises(ValidationError):
            ResourceMetadataInput(
                id="res_test",
                name="",
                kind=ResourceKind.FILE,
                access=ResourceAccess.READ,
            )


class TestProfilingInput:
    """测试 ProfilingInput 聚合输入模型"""

    def test_create_profiling_input_with_documents_only(self):
        """测试仅文档的聚合输入"""
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Identity\nName: Test Bot",
        )
        rules_doc = MarkdownDocument(
            doc_type=DocType.RULES,
            content="# Constraints\n- No external access",
        )

        profiling_input = ProfilingInput(
            worker_id="wrk_test_001",
            documents=[soul_doc, rules_doc],
        )

        assert profiling_input.worker_id == "wrk_test_001"
        assert len(profiling_input.documents) == 2
        assert profiling_input.skills == []
        assert profiling_input.resources == []

    def test_create_profiling_input_with_all_sources(self):
        """测试包含所有来源的聚合输入"""
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
            content="# Test",
        )
        skill = SkillMetadataInput(
            name="search",
            source=SkillSource.BUILTIN,
            trust_level=TrustLevel.TRUSTED,
        )
        resource = ResourceMetadataInput(
            id="res_wiki",
            name="Wiki",
            kind=ResourceKind.API,
            access=ResourceAccess.READ,
        )

        profiling_input = ProfilingInput(
            worker_id="wrk_test_001",
            documents=[soul_doc],
            skills=[skill],
            resources=[resource],
        )

        assert len(profiling_input.documents) == 1
        assert len(profiling_input.skills) == 1
        assert len(profiling_input.resources) == 1

    def test_profiling_input_empty_documents_raises_error(self):
        """测试没有文档的输入抛出错误"""
        from src.domain.models.profiling_input import ProfilingInput

        with pytest.raises(ValidationError) as exc_info:
            ProfilingInput(
                worker_id="wrk_test_001",
                documents=[],
            )

        assert "documents" in str(exc_info.value).lower()

    def test_profiling_input_invalid_worker_id_raises_error(self):
        """测试无效 Worker ID 格式抛出错误"""
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Test",
        )

        with pytest.raises(ValidationError):
            ProfilingInput(
                worker_id="invalid_id",  # 缺少 wrk_ 前缀
                documents=[doc],
            )

    def test_profiling_input_get_documents_by_type(self):
        """测试按类型获取文档"""
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Soul",
        )
        rules_doc = MarkdownDocument(
            doc_type=DocType.RULES,
            content="# Rules",
        )
        memory_doc = MarkdownDocument(
            doc_type=DocType.MEMORY,
            content="# Memory",
        )

        profiling_input = ProfilingInput(
            worker_id="wrk_test_001",
            documents=[soul_doc, rules_doc, memory_doc],
        )

        soul_docs = profiling_input.get_documents_by_type(DocType.SOUL)
        assert len(soul_docs) == 1
        assert soul_docs[0].content == "# Soul"

        rules_docs = profiling_input.get_documents_by_type(DocType.RULES)
        assert len(rules_docs) == 1

    def test_profiling_input_has_document_type(self):
        """测试检查是否有某类型文档"""
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Soul",
        )

        profiling_input = ProfilingInput(
            worker_id="wrk_test_001",
            documents=[soul_doc],
        )

        assert profiling_input.has_document_type(DocType.SOUL) is True
        assert profiling_input.has_document_type(DocType.RULES) is False