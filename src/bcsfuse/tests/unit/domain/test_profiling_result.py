"""
Tests for Profiling Result Domain Models

M2: Worker Profiling & Extraction

测试范围：
- SourceReference: 来源引用
- ExtractionWarning: 抽取警告
- ExtractionError: 抽取错误
- ExtractedCapability: 抽取的能力
- ExtractedDomain: 抽取的领域
- ExtractedResponsibility: 抽取的职责
- ExtractedConstraint: 抽取的约束
- ExtractedEscalationTrigger: 抽取的上报触发点
- ExtractedCollaborationStyle: 抽取的协作风格
- ExtractedSkill: 抽取的技能
- ExtractedResource: 抽取的资源
- ExtractedMemoryEpisode: 抽取的记忆片段
- WorkerProfileExtractionResult: 聚合抽取结果
"""

from __future__ import annotations

import pytest
from datetime import datetime
from pydantic import ValidationError


class TestSourceReference:
    """测试 SourceReference 模型"""

    def test_create_source_reference_with_section(self):
        """测试创建带 section 的来源引用"""
        from src.domain.models.profiling_result import SourceReference, DocType

        ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Capabilities",
            snippet="Information Retrieval (expert)",
        )

        assert ref.doc_type == DocType.SOUL
        assert ref.doc_name == "SOUL.md"
        assert ref.section == "Capabilities"
        assert ref.snippet == "Information Retrieval (expert)"
        assert ref.heading is None
        assert ref.line_start is None
        assert ref.line_end is None

    def test_create_source_reference_with_heading(self):
        """测试创建带 heading 的来源引用"""
        from src.domain.models.profiling_result import SourceReference, DocType

        ref = SourceReference(
            doc_type=DocType.RULES,
            doc_name="RULES.md",
            heading="Constraints",
            snippet="- 禁止发送外部邮件",
        )

        assert ref.heading == "Constraints"
        assert ref.section is None

    def test_create_source_reference_with_line_range(self):
        """测试创建带行范围的来源引用"""
        from src.domain.models.profiling_result import SourceReference, DocType

        ref = SourceReference(
            doc_type=DocType.MEMORY,
            doc_name="MEMORY.md",
            line_start=10,
            line_end=15,
            snippet="该 bot 在任务中表现稳定",
        )

        assert ref.line_start == 10
        assert ref.line_end == 15

    def test_source_reference_requires_at_least_one_location(self):
        """测试来源引用必须至少有一个定位信息"""
        from src.domain.models.profiling_result import SourceReference, DocType

        # 无定位信息应该抛出错误
        with pytest.raises(ValidationError) as exc_info:
            SourceReference(
                doc_type=DocType.SOUL,
                doc_name="SOUL.md",
            )

        assert "location" in str(exc_info.value).lower() or "至少" in str(exc_info.value)

    def test_source_reference_string_representation(self):
        """测试来源引用的字符串表示"""
        from src.domain.models.profiling_result import SourceReference, DocType

        ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Capabilities",
            snippet="test",
        )

        str_repr = str(ref)
        assert "SOUL.md" in str_repr
        assert "Capabilities" in str_repr


class TestExtractionWarning:
    """测试 ExtractionWarning 模型"""

    def test_create_warning_success(self):
        """测试创建警告"""
        from src.domain.models.profiling_result import ExtractionWarning, DocType

        warning = ExtractionWarning(
            field="constraints[0].severity",
            message="无法确定约束严重程度，使用默认值 medium",
            doc_type=DocType.RULES,
            doc_name="RULES.md",
            suggestion="请在文档中明确指定严重程度",
        )

        assert warning.field == "constraints[0].severity"
        assert "无法确定" in warning.message
        assert warning.doc_type == DocType.RULES
        assert warning.suggestion == "请在文档中明确指定严重程度"

    def test_create_warning_minimal(self):
        """测试创建最小警告"""
        from src.domain.models.profiling_result import ExtractionWarning, DocType

        warning = ExtractionWarning(
            field="capabilities",
            message="文档中未找到能力描述",
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
        )

        assert warning.field == "capabilities"
        assert warning.suggestion is None


class TestExtractionError:
    """测试 ExtractionError 模型"""

    def test_create_error_success(self):
        """测试创建错误"""
        from src.domain.models.profiling_result import ExtractionError, DocType

        error = ExtractionError(
            field="domains",
            message="无法解析领域标签，格式不正确",
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            severity="high",
        )

        assert error.field == "domains"
        assert error.severity == "high"

    def test_error_default_severity(self):
        """测试错误默认严重程度"""
        from src.domain.models.profiling_result import ExtractionError, DocType

        error = ExtractionError(
            field="test",
            message="test error",
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
        )

        assert error.severity == "medium"


class TestExtractedCapability:
    """测试 ExtractedCapability 模型"""

    def test_create_extracted_capability_success(self):
        """测试创建抽取的能力"""
        from src.domain.models.profiling_result import (
            ExtractedCapability,
            SourceReference,
            DocType,
            CapabilityLevel,
        )

        source_ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Capabilities",
            snippet="Information Retrieval (expert)",
        )

        capability = ExtractedCapability(
            name="Information Retrieval",
            level=CapabilityLevel.EXPERT,
            confidence=0.95,
            source_ref=source_ref,
        )

        assert capability.name == "Information Retrieval"
        assert capability.level == CapabilityLevel.EXPERT
        assert capability.confidence == 0.95
        assert capability.source_ref.doc_name == "SOUL.md"

    def test_confidence_out_of_range_raises_error(self):
        """测试置信度超出范围抛出错误"""
        from src.domain.models.profiling_result import (
            ExtractedCapability,
            SourceReference,
            DocType,
            CapabilityLevel,
        )

        source_ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Capabilities",
            snippet="test",
        )

        with pytest.raises(ValidationError):
            ExtractedCapability(
                name="Test",
                level=CapabilityLevel.NOVICE,
                confidence=1.5,  # 超出 0-1 范围
                source_ref=source_ref,
            )


class TestExtractedDomain:
    """测试 ExtractedDomain 模型"""

    def test_create_extracted_domain_success(self):
        """测试创建抽取的领域"""
        from src.domain.models.profiling_result import (
            ExtractedDomain,
            SourceReference,
            DocType,
        )

        source_ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Domains",
            snippet="architecture",
        )

        domain = ExtractedDomain(
            name="architecture",
            confidence=0.9,
            source_ref=source_ref,
        )

        assert domain.name == "architecture"
        assert domain.confidence == 0.9


class TestExtractedResponsibility:
    """测试 ExtractedResponsibility 模型"""

    def test_create_extracted_responsibility_success(self):
        """测试创建抽取的职责"""
        from src.domain.models.profiling_result import (
            ExtractedResponsibility,
            SourceReference,
            DocType,
        )

        source_ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            heading="Responsibilities",
            snippet="Research and information gathering",
        )

        responsibility = ExtractedResponsibility(
            description="Research and information gathering",
            confidence=0.85,
            source_ref=source_ref,
        )

        assert responsibility.description == "Research and information gathering"
        assert responsibility.confidence == 0.85


class TestExtractedConstraint:
    """测试 ExtractedConstraint 模型"""

    def test_create_extracted_constraint_forbidden(self):
        """测试创建禁止类型约束"""
        from src.domain.models.profiling_result import (
            ExtractedConstraint,
            SourceReference,
            DocType,
            ConstraintPolicy,
            ConstraintKind,
        )

        source_ref = SourceReference(
            doc_type=DocType.RULES,
            doc_name="RULES.md",
            section="Constraints",
            snippet="禁止发送外部邮件",
        )

        constraint = ExtractedConstraint(
            kind=ConstraintKind.POLICY,
            rule="禁止发送外部邮件",
            policy=ConstraintPolicy.FORBIDDEN,
            severity="high",
            confidence=0.95,
            source_ref=source_ref,
        )

        assert constraint.policy == ConstraintPolicy.FORBIDDEN
        assert constraint.rule == "禁止发送外部邮件"
        assert constraint.kind == ConstraintKind.POLICY

    def test_create_extracted_constraint_approval_required(self):
        """测试创建需审批约束"""
        from src.domain.models.profiling_result import (
            ExtractedConstraint,
            SourceReference,
            DocType,
            ConstraintPolicy,
            ConstraintKind,
        )

        source_ref = SourceReference(
            doc_type=DocType.RULES,
            doc_name="RULES.md",
            section="Constraints",
            snippet="写生产数据库前必须审批",
        )

        constraint = ExtractedConstraint(
            kind=ConstraintKind.APPROVAL,
            rule="写生产数据库前必须审批",
            policy=ConstraintPolicy.APPROVAL_REQUIRED,
            severity="critical",
            confidence=0.9,
            source_ref=source_ref,
        )

        assert constraint.policy == ConstraintPolicy.APPROVAL_REQUIRED
        assert constraint.severity == "critical"


class TestExtractedEscalationTrigger:
    """测试 ExtractedEscalationTrigger 模型"""

    def test_create_escalation_trigger_success(self):
        """测试创建上报触发点"""
        from src.domain.models.profiling_result import (
            ExtractedEscalationTrigger,
            SourceReference,
            DocType,
        )

        source_ref = SourceReference(
            doc_type=DocType.RULES,
            doc_name="RULES.md",
            section="Escalation",
            snippet="遇到高安全风险操作时上报",
        )

        trigger = ExtractedEscalationTrigger(
            condition="遇到高安全风险操作",
            action="上报给 human reviewer",
            confidence=0.9,
            source_ref=source_ref,
        )

        assert trigger.condition == "遇到高安全风险操作"
        assert trigger.action == "上报给 human reviewer"


class TestExtractedCollaborationStyle:
    """测试 ExtractedCollaborationStyle 模型"""

    def test_create_collaboration_style_success(self):
        """测试创建协作风格"""
        from src.domain.models.profiling_result import (
            ExtractedCollaborationStyle,
            SourceReference,
            DocType,
        )

        source_ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Collaboration Style",
            snippet="prefer async communication",
        )

        style = ExtractedCollaborationStyle(
            preference="async_communication",
            details="Prefer asynchronous communication, respond within 24 hours",
            confidence=0.8,
            source_ref=source_ref,
        )

        assert style.preference == "async_communication"
        assert "asynchronous" in style.details


class TestExtractedSkill:
    """测试 ExtractedSkill 模型"""

    def test_create_extracted_skill_from_document(self):
        """测试从文档抽取的技能"""
        from src.domain.models.profiling_result import (
            ExtractedSkill,
            SourceReference,
            DocType,
            SkillSource,
            TrustLevel,
        )

        source_ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Skills",
            snippet="web_search (builtin, trusted)",
        )

        skill = ExtractedSkill(
            name="web_search",
            skill_source=SkillSource.BUILTIN,
            trust_level=TrustLevel.TRUSTED,
            approval_required=False,
            confidence=0.95,
            source_ref=source_ref,
        )

        assert skill.name == "web_search"
        assert skill.skill_source == SkillSource.BUILTIN
        assert skill.trust_level == TrustLevel.TRUSTED


class TestExtractedResource:
    """测试 ExtractedResource 模型"""

    def test_create_extracted_resource_success(self):
        """测试创建抽取的资源"""
        from src.domain.models.profiling_result import (
            ExtractedResource,
            SourceReference,
            DocType,
            ResourceKind,
            ResourceAccess,
        )

        source_ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Resources",
            snippet="Internal Wiki (read access)",
        )

        resource = ExtractedResource(
            id="res_internal_wiki",
            name="Internal Wiki",
            kind=ResourceKind.API,
            access=ResourceAccess.READ,
            confidence=0.9,
            source_ref=source_ref,
        )

        assert resource.id == "res_internal_wiki"
        assert resource.kind == ResourceKind.API
        assert resource.access == ResourceAccess.READ


class TestExtractedMemoryEpisode:
    """测试 ExtractedMemoryEpisode 模型"""

    def test_create_memory_episode_success(self):
        """测试创建记忆片段"""
        from src.domain.models.profiling_result import (
            ExtractedMemoryEpisode,
            SourceReference,
            DocType,
        )

        source_ref = SourceReference(
            doc_type=DocType.MEMORY,
            doc_name="MEMORY.md",
            line_start=1,
            line_end=5,
            snippet="该 bot 在多 agent 组队任务中表现稳定",
        )

        episode = ExtractedMemoryEpisode(
            timestamp="2026-03-01",
            summary="在多 agent 组队任务中表现稳定",
            task_type="multi_agent_team_composition",
            outcome="success",
            confidence=0.85,
            source_ref=source_ref,
        )

        assert episode.timestamp == "2026-03-01"
        assert episode.summary == "在多 agent 组队任务中表现稳定"
        assert episode.outcome == "success"

    def test_memory_episode_optional_fields(self):
        """测试记忆片段的可选字段"""
        from src.domain.models.profiling_result import (
            ExtractedMemoryEpisode,
            SourceReference,
            DocType,
        )

        source_ref = SourceReference(
            doc_type=DocType.MEMORY,
            doc_name="MEMORY.md",
            section="Episodes",
            snippet="test",
        )

        episode = ExtractedMemoryEpisode(
            summary="Test episode",
            confidence=0.7,
            source_ref=source_ref,
        )

        assert episode.timestamp is None
        assert episode.task_type is None
        assert episode.outcome is None


class TestWorkerProfileExtractionResult:
    """测试 WorkerProfileExtractionResult 聚合结果模型"""

    def test_create_extraction_result_success(self):
        """测试创建抽取结果"""
        from src.domain.models.profiling_result import (
            WorkerProfileExtractionResult,
            ExtractedCapability,
            ExtractedDomain,
            SourceReference,
            DocType,
            CapabilityLevel,
        )

        source_ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Capabilities",
            snippet="test",
        )

        capability = ExtractedCapability(
            name="Test Capability",
            level=CapabilityLevel.ADVANCED,
            confidence=0.9,
            source_ref=source_ref,
        )

        domain = ExtractedDomain(
            name="architecture",
            confidence=0.8,
            source_ref=source_ref,
        )

        result = WorkerProfileExtractionResult(
            worker_id="wrk_test_001",
            capabilities=[capability],
            domains=[domain],
        )

        assert result.worker_id == "wrk_test_001"
        assert len(result.capabilities) == 1
        assert len(result.domains) == 1
        assert len(result.warnings) == 0
        assert len(result.errors) == 0

    def test_extraction_result_with_warnings_and_errors(self):
        """测试包含警告和错误的抽取结果"""
        from src.domain.models.profiling_result import (
            WorkerProfileExtractionResult,
            ExtractionWarning,
            ExtractionError,
            DocType,
        )

        warning = ExtractionWarning(
            field="constraints",
            message="未找到约束条件",
            doc_type=DocType.RULES,
            doc_name="RULES.md",
        )

        error = ExtractionError(
            field="responsibilities",
            message="无法解析职责列表",
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
        )

        result = WorkerProfileExtractionResult(
            worker_id="wrk_test_001",
            capabilities=[],
            domains=[],
            warnings=[warning],
            errors=[error],
        )

        assert len(result.warnings) == 1
        assert len(result.errors) == 1
        assert not result.is_complete()

    def test_extraction_result_is_complete(self):
        """测试判断抽取结果是否完整"""
        from src.domain.models.profiling_result import (
            WorkerProfileExtractionResult,
            ExtractedCapability,
            ExtractionError,
            SourceReference,
            DocType,
            CapabilityLevel,
        )

        source_ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Capabilities",
            snippet="test",
        )

        capability = ExtractedCapability(
            name="Test",
            level=CapabilityLevel.NOVICE,
            confidence=0.5,
            source_ref=source_ref,
        )

        # 无错误时完整
        result_no_errors = WorkerProfileExtractionResult(
            worker_id="wrk_test_001",
            capabilities=[capability],
            domains=[],
            warnings=[],  # 警告不影响完整性
            errors=[],
        )
        assert result_no_errors.is_complete()

        # 有错误时不完整
        result_with_errors = WorkerProfileExtractionResult(
            worker_id="wrk_test_001",
            capabilities=[capability],
            domains=[],
            warnings=[],
            errors=[ExtractionError(
                field="test",
                message="error",
                doc_type=DocType.SOUL,
                doc_name="SOUL.md",
            )],
        )
        assert not result_with_errors.is_complete()

    def test_extraction_result_summary(self):
        """测试抽取结果的摘要"""
        from src.domain.models.profiling_result import (
            WorkerProfileExtractionResult,
            ExtractedCapability,
            ExtractedConstraint,
            SourceReference,
            DocType,
            CapabilityLevel,
            ConstraintPolicy,
            ConstraintKind,
        )

        source_ref = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Capabilities",
            snippet="test",
        )

        capability = ExtractedCapability(
            name="Test",
            level=CapabilityLevel.NOVICE,
            confidence=0.5,
            source_ref=source_ref,
        )

        constraint = ExtractedConstraint(
            kind=ConstraintKind.POLICY,
            rule="Test rule",
            policy=ConstraintPolicy.ALLOWED,
            confidence=0.8,
            source_ref=source_ref,
        )

        result = WorkerProfileExtractionResult(
            worker_id="wrk_test_001",
            capabilities=[capability, capability],
            domains=[],
            constraints=[constraint],
        )

        summary = result.get_summary()
        assert summary["capabilities_count"] == 2
        assert summary["constraints_count"] == 1
        assert summary["warnings_count"] == 0
        assert summary["errors_count"] == 0

    def test_extraction_result_merge(self):
        """测试合并两个抽取结果"""
        from src.domain.models.profiling_result import (
            WorkerProfileExtractionResult,
            ExtractedCapability,
            ExtractedDomain,
            ExtractionWarning,
            SourceReference,
            DocType,
            CapabilityLevel,
        )

        source_ref1 = SourceReference(
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
            section="Capabilities",
            snippet="test",
        )

        source_ref2 = SourceReference(
            doc_type=DocType.RULES,
            doc_name="RULES.md",
            section="Constraints",
            snippet="test",
        )

        capability = ExtractedCapability(
            name="Capability1",
            level=CapabilityLevel.NOVICE,
            confidence=0.5,
            source_ref=source_ref1,
        )

        domain = ExtractedDomain(
            name="architecture",
            confidence=0.8,
            source_ref=source_ref2,
        )

        warning = ExtractionWarning(
            field="test",
            message="warning",
            doc_type=DocType.SOUL,
            doc_name="SOUL.md",
        )

        result1 = WorkerProfileExtractionResult(
            worker_id="wrk_test_001",
            capabilities=[capability],
            domains=[],
            warnings=[warning],
        )

        result2 = WorkerProfileExtractionResult(
            worker_id="wrk_test_001",
            capabilities=[],
            domains=[domain],
        )

        merged = result1.merge(result2)

        assert len(merged.capabilities) == 1
        assert len(merged.domains) == 1
        assert len(merged.warnings) == 1
        assert merged.worker_id == "wrk_test_001"