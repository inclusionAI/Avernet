"""
Tests for Baseline Profile Extractor

M2: Worker Profiling & Extraction

测试范围：
- SOUL.md 基础抽取测试
- RULES.md 约束抽取测试
- MEMORY.md episodic memory 抽取测试
- 空文档/噪音文档/冲突信息测试
- 部分缺失字段测试
- 来源追踪测试
- warning/error 测试

Baseline 实现：
- 规则可解释
- 优先基于标题、列表、标签、简单模式匹配
- 允许保守抽取，不要求复杂 NLP
- 可部分成功
"""

from __future__ import annotations

import pytest


class TestBaselineProfileExtractor:
    """测试 BaselineProfileExtractor 基本功能"""

    def test_extractor_importable(self):
        """测试 Extractor 可导入"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor

        assert BaselineProfileExtractor is not None

    def test_extractor_implements_protocol(self):
        """测试 Extractor 实现 ProfileExtractor 协议"""
        from src.domain.services.profile_extractor import ProfileExtractor
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor

        extractor = BaselineProfileExtractor()
        assert isinstance(extractor, ProfileExtractor)


class TestSOULExtraction:
    """测试 SOUL.md 抽取"""

    def test_extract_capabilities_from_soul(self):
        """测试从 SOUL.md 抽取能力"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_content = """
# Identity
Name: Research Bot
Role: Research Assistant

# Capabilities
- Information Retrieval (expert)
- Data Analysis (advanced)
- Report Generation (intermediate)

# Domains
- architecture
- documentation
"""
        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content=soul_content,
        )

        input_data = ProfilingInput(
            worker_id="wrk_test_001",
            documents=[soul_doc],
        )

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.capabilities) == 3
        assert any(c.name == "Information Retrieval" for c in result.capabilities)
        assert all(c.source_ref.doc_name == "SOUL.md" for c in result.capabilities)

    def test_extract_capability_levels(self):
        """测试抽取能力级别"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )
        from src.domain.models.profiling_result import CapabilityLevel

        soul_content = """
# Capabilities
- Information Retrieval (expert)
- Data Analysis (advanced)
- Basic Task (novice)
"""
        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=soul_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        expert_cap = next((c for c in result.capabilities if "Retrieval" in c.name), None)
        assert expert_cap is not None
        assert expert_cap.level == CapabilityLevel.EXPERT

        advanced_cap = next((c for c in result.capabilities if "Analysis" in c.name), None)
        assert advanced_cap.level == CapabilityLevel.ADVANCED

    def test_extract_domains_from_soul(self):
        """测试从 SOUL.md 抽取领域"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_content = """
# Domains
- architecture
- machine_learning
- security
"""
        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=soul_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.domains) == 3
        domain_names = [d.name for d in result.domains]
        assert "architecture" in domain_names
        assert "machine_learning" in domain_names

    def test_extract_responsibilities_from_soul(self):
        """测试从 SOUL.md 抽取职责"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_content = """
# Responsibilities
- Research and information gathering
- Data analysis and reporting
- Team coordination
"""
        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=soul_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.responsibilities) == 3
        assert any("Research" in r.description for r in result.responsibilities)

    def test_extract_collaboration_style_from_soul(self):
        """测试从 SOUL.md 抽取协作风格"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_content = """
# Collaboration Style
Preference: async_communication
Details: Prefer asynchronous communication with 24-hour response time.
"""
        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=soul_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert result.collaboration_style is not None
        assert result.collaboration_style.preference == "async_communication"

    def test_extract_skills_from_soul(self):
        """测试从 SOUL.md 抽取技能"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_content = """
# Skills
- web_search (builtin, trusted)
- data_processing (managed, guarded)
- email_sender (plugin, sandbox_only, approval_required)
"""
        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=soul_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.skills) >= 1
        web_search = next((s for s in result.skills if "web_search" in s.name), None)
        assert web_search is not None

    def test_extract_resources_from_soul(self):
        """测试从 SOUL.md 抽取资源"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_content = """
# Resources
- Internal Wiki (api, read)
- Project Repository (repo, write)
- Analytics Dashboard (dashboard, read)
"""
        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=soul_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.resources) >= 1


class TestRULESExtraction:
    """测试 RULES.md 抽取"""

    def test_extract_forbidden_constraints(self):
        """测试抽取禁止约束"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )
        from src.domain.models.profiling_result import ConstraintPolicy

        rules_content = """
# Constraints
- 禁止发送外部邮件
- 禁止访问生产数据库
- 可读取内部文档
"""
        rules_doc = MarkdownDocument(doc_type=DocType.RULES, content=rules_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[rules_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.constraints) >= 2

        forbidden = [c for c in result.constraints if c.policy == ConstraintPolicy.FORBIDDEN]
        assert len(forbidden) >= 2

    def test_extract_approval_required_constraints(self):
        """测试抽取需审批约束"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )
        from src.domain.models.profiling_result import ConstraintPolicy

        rules_content = """
# Constraints
- 写生产数据库前必须审批
- 发送外部邮件需要审批
- 可读取内部文档
"""
        rules_doc = MarkdownDocument(doc_type=DocType.RULES, content=rules_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[rules_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        approval_required = [c for c in result.constraints if c.policy == ConstraintPolicy.APPROVAL_REQUIRED]
        assert len(approval_required) >= 2

    def test_extract_escalation_triggers(self):
        """测试抽取上报触发点"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        rules_content = """
# Escalation Triggers
- 遇到高安全风险操作时上报给 human reviewer
- 超出权限范围时上报给 team lead
- 数据异常时上报给 data team
"""
        rules_doc = MarkdownDocument(doc_type=DocType.RULES, content=rules_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[rules_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.escalation_triggers) >= 1
        assert any("human reviewer" in t.action for t in result.escalation_triggers)


class TestMEMORYExtraction:
    """测试 MEMORY.md 抽取"""

    def test_extract_memory_episodes(self):
        """测试抽取记忆片段"""
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
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[memory_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.memory_episodes) >= 1
        assert any("组队" in e.summary or "调研" in e.summary for e in result.memory_episodes)

    def test_memory_episodes_have_timestamps(self):
        """测试记忆片段有时间戳"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        memory_content = """
# 2026-03-01
该 bot 在任务中表现稳定
"""
        memory_doc = MarkdownDocument(doc_type=DocType.MEMORY, content=memory_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[memory_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.memory_episodes) >= 1
        assert result.memory_episodes[0].timestamp is not None


class TestSourceReference:
    """测试来源引用"""

    def test_capabilities_have_source_reference(self):
        """测试能力有来源引用"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_content = """
# Capabilities
- Information Retrieval (expert)
"""
        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=soul_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.capabilities) >= 1
        cap = result.capabilities[0]
        assert cap.source_ref is not None
        assert cap.source_ref.doc_type == DocType.SOUL
        assert cap.source_ref.doc_name == "SOUL.md"
        # 至少有一种定位信息
        has_location = (
            cap.source_ref.section is not None or
            cap.source_ref.heading is not None or
            cap.source_ref.line_start is not None
        )
        assert has_location

    def test_constraints_have_source_reference(self):
        """测试约束有来源引用"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        rules_content = """
# Constraints
- 禁止发送外部邮件
"""
        rules_doc = MarkdownDocument(doc_type=DocType.RULES, content=rules_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[rules_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.constraints) >= 1
        constraint = result.constraints[0]
        assert constraint.source_ref.doc_type == DocType.RULES


class TestEmptyAndNoisyDocuments:
    """测试空文档/噪音文档处理"""

    def test_empty_document_returns_warnings(self):
        """测试空文档产生警告"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Title\n\nSome content without structured sections.",
        )
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        # 没有抽取到能力时应有警告
        if len(result.capabilities) == 0:
            assert len(result.warnings) >= 1
            assert any("capabilit" in w.message.lower() for w in result.warnings)

    def test_noisy_document_still_works(self):
        """测试噪音文档仍能工作"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        noisy_content = """
# Random Title

```python
def some_code():
    pass
```

# Capabilities
- Real Capability (expert)

[Link](http://example.com)

**Bold text** and *italic text*
"""
        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=noisy_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        # 应该能抽取到能力
        assert len(result.capabilities) >= 1
        assert any("Real Capability" in c.name for c in result.capabilities)


class TestPartialSuccess:
    """测试部分成功"""

    def test_partial_extraction_success(self):
        """测试部分抽取成功"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
        )

        soul_content = """
# Capabilities
- Info Retrieval (expert)

# Missing Sections
Here is some content without proper structure.
"""
        soul_doc = MarkdownDocument(doc_type=DocType.SOUL, content=soul_content)
        input_data = ProfilingInput(worker_id="wrk_test_001", documents=[soul_doc])

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        # 应该能抽取到能力
        assert len(result.capabilities) >= 1
        # 应该是成功的（无错误）
        assert result.is_complete()

    def test_multiple_documents_merge(self):
        """测试多个文档合并抽取"""
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
        rules_doc = MarkdownDocument(
            doc_type=DocType.RULES,
            content="# Constraints\n- 禁止外部访问",
        )

        input_data = ProfilingInput(
            worker_id="wrk_test_001",
            documents=[soul_doc, rules_doc],
        )

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.capabilities) >= 1
        assert len(result.constraints) >= 1


class TestSkillMetadataInput:
    """测试技能元数据输入"""

    def test_skill_metadata_converted_to_extracted_skill(self):
        """测试技能元数据转换为抽取技能"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
            SkillMetadataInput,
            SkillSource,
            TrustLevel,
        )

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Test",
        )
        skill = SkillMetadataInput(
            name="web_search",
            source=SkillSource.BUILTIN,
            trust_level=TrustLevel.TRUSTED,
        )

        input_data = ProfilingInput(
            worker_id="wrk_test_001",
            documents=[soul_doc],
            skills=[skill],
        )

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.skills) >= 1
        extracted_skill = next((s for s in result.skills if s.name == "web_search"), None)
        assert extracted_skill is not None


class TestResourceMetadataInput:
    """测试资源元数据输入"""

    def test_resource_metadata_converted_to_extracted_resource(self):
        """测试资源元数据转换为抽取资源"""
        from src.infra.extractors.baseline_profile_extractor import BaselineProfileExtractor
        from src.domain.models.profiling_input import (
            ProfilingInput,
            MarkdownDocument,
            DocType,
            ResourceMetadataInput,
            ResourceKind,
            ResourceAccess,
        )

        soul_doc = MarkdownDocument(
            doc_type=DocType.SOUL,
            content="# Test",
        )
        resource = ResourceMetadataInput(
            id="res_wiki",
            name="Wiki",
            kind=ResourceKind.API,
            access=ResourceAccess.READ,
        )

        input_data = ProfilingInput(
            worker_id="wrk_test_001",
            documents=[soul_doc],
            resources=[resource],
        )

        extractor = BaselineProfileExtractor()
        result = extractor.extract(input_data)

        assert len(result.resources) >= 1
        extracted_res = next((r for r in result.resources if r.id == "res_wiki"), None)
        assert extracted_res is not None