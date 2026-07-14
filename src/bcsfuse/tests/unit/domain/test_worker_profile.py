"""
Tests for Worker Profile Domain Model

Worker Profile Ingestion Baseline

测试范围：
- ProfileType: 画像类型枚举
- SourceType: 来源类型枚举
- WorkerProfileWarning: 警告模型
- WorkerProfile: 归一化画像模型
- WorkerProfileScanResult: 扫描结果模型
- ProfileMatchResult: 匹配结果模型
- ProfileSearchResult: 搜索结果模型
- ProfileRecommendResult: 推荐结果模型
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestProfileType:
    """测试 ProfileType 枚举"""

    def test_profile_type_values(self):
        """测试枚举值定义"""
        from src.domain.models.worker_profile import ProfileType

        assert ProfileType.DEFAULT == "default"
        assert ProfileType.BOT == "bot"

    def test_profile_type_from_string(self):
        """测试从字符串创建枚举"""
        from src.domain.models.worker_profile import ProfileType

        assert ProfileType("default") == ProfileType.DEFAULT
        assert ProfileType("bot") == ProfileType.BOT


class TestSourceType:
    """测试 SourceType 枚举"""

    def test_source_type_values(self):
        """测试枚举值定义"""
        from src.domain.models.worker_profile import SourceType

        assert SourceType.FILE == "file"
        assert SourceType.API == "api"
        assert SourceType.REGISTRY == "registry"

    def test_source_type_from_string(self):
        """测试从字符串创建枚举"""
        from src.domain.models.worker_profile import SourceType

        assert SourceType("file") == SourceType.FILE


class TestWorkerProfileWarning:
    """测试 WorkerProfileWarning 模型"""

    def test_create_warning_success(self):
        """测试创建警告"""
        from src.domain.models.worker_profile import WorkerProfileWarning

        warning = WorkerProfileWarning(
            code="SKILL_FILE_NOT_FOUND",
            message="skill_sets.json not found",
            source_path="/data/staff_001/default/openclaw/skills",
            suggestion="Please add skill_sets.json file",
        )

        assert warning.code == "SKILL_FILE_NOT_FOUND"
        assert warning.message == "skill_sets.json not found"
        assert warning.source_path == "/data/staff_001/default/openclaw/skills"
        assert warning.suggestion == "Please add skill_sets.json file"

    def test_create_warning_minimal(self):
        """测试创建最小字段警告"""
        from src.domain.models.worker_profile import WorkerProfileWarning

        warning = WorkerProfileWarning(
            code="NO_ACTIVE_SKILL_SET",
            message="No active skill set found",
        )

        assert warning.code == "NO_ACTIVE_SKILL_SET"
        assert warning.message == "No active skill set found"
        assert warning.source_path is None
        assert warning.suggestion is None

    def test_warning_missing_required_fields(self):
        """测试缺少必填字段"""
        from src.domain.models.worker_profile import WorkerProfileWarning

        with pytest.raises(ValidationError):
            WorkerProfileWarning(message="test")  # 缺少 code

        with pytest.raises(ValidationError):
            WorkerProfileWarning(code="TEST")  # 缺少 message

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        from src.domain.models.worker_profile import WorkerProfileWarning

        with pytest.raises(ValidationError):
            WorkerProfileWarning(
                code="TEST",
                message="test",
                extra_field="not_allowed",  # type: ignore
            )


class TestWorkerProfile:
    """测试 WorkerProfile 模型"""

    def test_create_worker_profile_success(self):
        """测试创建 Worker Profile"""
        from src.domain.models.worker_profile import (
            WorkerProfile,
            ProfileType,
            SourceType,
        )
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.skill_profile import SkillProfile

        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="# Test Bot",
            source_path="/data/staff_001/default/openclaw/SOUL.md",
        )

        skill = SkillProfile(
            name="search",
            skill_id="search_v1",
            skill_set_name="default",
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_type=SourceType.FILE,
            source_root="/data/bolt_data",
            context_fragments=[fragment],
            active_skills=[skill],
        )

        assert profile.staff_id == "001"
        assert profile.profile_id == "default"
        assert profile.profile_type == ProfileType.DEFAULT
        assert profile.source_type == SourceType.FILE
        assert profile.source_root == "/data/bolt_data"
        assert len(profile.context_fragments) == 1
        assert len(profile.active_skills) == 1

    def test_create_worker_profile_minimal(self):
        """测试创建最小字段 Worker Profile"""
        from src.domain.models.worker_profile import (
            WorkerProfile,
            ProfileType,
            SourceType,
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data/bolt_data",
        )

        assert profile.staff_id == "001"
        assert profile.profile_id == "default"
        assert profile.context_fragments == []
        assert profile.active_skills == []
        assert profile.warnings == []
        assert profile.searchable_text == ""
        assert profile.source_type == SourceType.FILE  # 默认值

    def test_create_bot_profile(self):
        """测试创建 Bot 类型的 Profile"""
        from src.domain.models.worker_profile import (
            WorkerProfile,
            ProfileType,
            SourceType,
        )

        profile = WorkerProfile(
            staff_id="260065",
            profile_id="20260319_qjmzo9k6",
            profile_type=ProfileType.BOT,
            source_root="/data/bolt_data",
        )

        assert profile.profile_type == ProfileType.BOT
        assert profile.profile_id == "20260319_qjmzo9k6"

    def test_profile_key_property(self):
        """测试 profile_key 属性"""
        from src.domain.models.worker_profile import (
            WorkerProfile,
            ProfileType,
        )

        # DEFAULT 类型
        profile = WorkerProfile(
            staff_id="260065",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data/bolt_data",
        )
        assert profile.profile_key == "staff_260065:default"

        # BOT 类型
        profile = WorkerProfile(
            staff_id="260065",
            profile_id="20260319_qjmzo9k6",
            profile_type=ProfileType.BOT,
            source_root="/data/bolt_data",
        )
        assert profile.profile_key == "staff_260065:20260319_qjmzo9k6"

    def test_missing_required_fields_raises_error(self):
        """测试缺少必填字段抛出错误"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType

        # 缺少 staff_id
        with pytest.raises(ValidationError):
            WorkerProfile(
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/data",
            )

        # 缺少 profile_id
        with pytest.raises(ValidationError):
            WorkerProfile(
                staff_id="001",
                profile_type=ProfileType.DEFAULT,
                source_root="/data",
            )

        # 缺少 profile_type
        with pytest.raises(ValidationError):
            WorkerProfile(
                staff_id="001",
                profile_id="default",
                source_root="/data",
            )

        # 缺少 source_root
        with pytest.raises(ValidationError):
            WorkerProfile(
                staff_id="001",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
            )

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType

        with pytest.raises(ValidationError):
            WorkerProfile(
                staff_id="001",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/data",
                extra_field="not_allowed",  # type: ignore
            )

    def test_empty_staff_id_raises_error(self):
        """测试空 staff_id 抛出错误"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType

        with pytest.raises(ValidationError):
            WorkerProfile(
                staff_id="",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/data",
            )

    def test_empty_profile_id_raises_error(self):
        """测试空 profile_id 抛出错误"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType

        with pytest.raises(ValidationError):
            WorkerProfile(
                staff_id="001",
                profile_id="",
                profile_type=ProfileType.DEFAULT,
                source_root="/data",
            )

    def test_empty_source_root_raises_error(self):
        """测试空 source_root 抛出错误"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType

        with pytest.raises(ValidationError):
            WorkerProfile(
                staff_id="001",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="",
            )


class TestWorkerProfileSearchableText:
    """测试 WorkerProfile 的 searchable_text 生成"""

    def test_generate_searchable_text_empty(self):
        """测试空内容的 searchable_text"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
        )

        profile.generate_searchable_text()
        assert profile.searchable_text == ""

    def test_generate_searchable_text_with_context_only(self):
        """测试仅有 context 的 searchable_text"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content="Test content for search",
            source_path="/test",
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
            context_fragments=[fragment],
        )

        profile.generate_searchable_text()

        # 格式: [CONTEXT:kind:content]
        assert "[CONTEXT:soul:Test content for search]" in profile.searchable_text

    def test_generate_searchable_text_with_skills_only(self):
        """测试仅有 skills 的 searchable_text"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType
        from src.domain.models.skill_profile import SkillProfile

        skill = SkillProfile(
            name="web_search",
            description="Search the web",
            skill_id="search_v1",
            skill_set_name="default",
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
            active_skills=[skill],
        )

        profile.generate_searchable_text()

        # 格式: [SKILL:name:description]
        assert "[SKILL:web_search:Search the web]" in profile.searchable_text

    def test_generate_searchable_text_fixed_order(self):
        """测试 searchable_text 的固定顺序"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType
        from src.domain.models.context_fragment import ContextFragment, ContextKind
        from src.domain.models.skill_profile import SkillProfile

        # 多个 context fragments（不同 kind）
        fragment1 = ContextFragment(
            kind=ContextKind.TOOLS,  # tools 在字母序中靠后
            filename="TOOLS.md",
            content="Tools content",
            source_path="/test",
        )
        fragment2 = ContextFragment(
            kind=ContextKind.AGENT,  # agent 在字母序中靠前
            filename="AGENTS.md",
            content="Agent content",
            source_path="/test",
        )

        # 多个 skills（不同 name）
        skill1 = SkillProfile(
            name="zebra_search",  # z 在字母序中靠后
            skill_id="z_v1",
            skill_set_name="default",
        )
        skill2 = SkillProfile(
            name="alpha_search",  # a 在字母序中靠前
            skill_id="a_v1",
            skill_set_name="default",
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
            context_fragments=[fragment1, fragment2],
            active_skills=[skill1, skill2],
        )

        profile.generate_searchable_text()

        # Context 在前，按 kind 字母序
        # Skills 在后，按 name 字母序
        text = profile.searchable_text
        agent_pos = text.find("[CONTEXT:agent:")
        tools_pos = text.find("[CONTEXT:tools:")
        alpha_pos = text.find("[SKILL:alpha_search:")
        zebra_pos = text.find("[SKILL:zebra_search:")

        # 验证顺序
        assert agent_pos < tools_pos  # agent < tools
        assert alpha_pos < zebra_pos  # alpha < zebra
        assert tools_pos < alpha_pos  # context 完成后才到 skills

    def test_generate_searchable_text_content_truncation(self):
        """测试 searchable_text 内容截断"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType
        from src.domain.models.context_fragment import ContextFragment, ContextKind

        # 创建超长内容
        long_content = "x" * 1000
        fragment = ContextFragment(
            kind=ContextKind.SOUL,
            filename="SOUL.md",
            content=long_content,
            source_path="/test",
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
            context_fragments=[fragment],
        )

        profile.generate_searchable_text()

        # 内容应被截断到 500 字符
        # 格式: [CONTEXT:soul:内容...]
        # 需要检查内容部分
        import re
        match = re.search(r"\[CONTEXT:soul:(.*?)\]", profile.searchable_text)
        assert match is not None
        content_part = match.group(1)
        assert len(content_part) == 500  # 截断到 500 字符

    def test_generate_searchable_text_skill_no_description(self):
        """测试无描述 skill 的 searchable_text"""
        from src.domain.models.worker_profile import WorkerProfile, ProfileType
        from src.domain.models.skill_profile import SkillProfile

        skill = SkillProfile(
            name="no_desc_skill",
            skill_id="nd_v1",
            skill_set_name="default",
            description=None,
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
            active_skills=[skill],
        )

        profile.generate_searchable_text()

        # 无描述时格式: [SKILL:name:]
        assert "[SKILL:no_desc_skill:]" in profile.searchable_text


class TestWorkerProfileScanResult:
    """测试 WorkerProfileScanResult 模型"""

    def test_create_scan_result_success(self):
        """测试创建扫描结果"""
        from src.domain.models.worker_profile import (
            WorkerProfileScanResult,
            WorkerProfile,
            ProfileType,
            WorkerProfileWarning,
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
        )

        warning = WorkerProfileWarning(
            code="DUPLICATE_PROFILE",
            message="Duplicate profile found",
        )

        result = WorkerProfileScanResult(
            profiles=[profile],
            scan_warnings=[warning],
            source_roots=["/data/root1", "/data/root2"],
        )

        assert len(result.profiles) == 1
        assert len(result.scan_warnings) == 1
        assert len(result.source_roots) == 2

    def test_total_warnings_property(self):
        """测试 total_warnings 属性"""
        from src.domain.models.worker_profile import (
            WorkerProfileScanResult,
            WorkerProfile,
            ProfileType,
            WorkerProfileWarning,
        )

        profile1 = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
            warnings=[
                WorkerProfileWarning(code="W1", message="warning 1"),
                WorkerProfileWarning(code="W2", message="warning 2"),
            ],
        )

        profile2 = WorkerProfile(
            staff_id="002",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
            warnings=[
                WorkerProfileWarning(code="W3", message="warning 3"),
            ],
        )

        result = WorkerProfileScanResult(
            profiles=[profile1, profile2],
            scan_warnings=[
                WorkerProfileWarning(code="S1", message="scan warning"),
            ],
            source_roots=["/data"],
        )

        # total = scan_warnings(1) + profile1.warnings(2) + profile2.warnings(1) = 4
        assert result.total_warnings == 4

    def test_empty_scan_result(self):
        """测试空扫描结果"""
        from src.domain.models.worker_profile import WorkerProfileScanResult

        result = WorkerProfileScanResult(
            profiles=[],
            scan_warnings=[],
            source_roots=[],
        )

        assert result.total_warnings == 0
        assert len(result.profiles) == 0


class TestProfileMatchResult:
    """测试 ProfileMatchResult 模型"""

    def test_create_match_result_success(self):
        """测试创建匹配结果"""
        from src.domain.models.worker_profile import (
            ProfileMatchResult,
            WorkerProfile,
            ProfileType,
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
        )

        match = ProfileMatchResult(
            profile=profile,
            score=0.85,
            matched_fields=["skills.web_search", "context.soul"],
            reasons=["Has matching skill: web_search", "Relevant context in SOUL.md"],
        )

        assert match.profile.staff_id == "001"
        assert match.score == 0.85
        assert len(match.matched_fields) == 2
        assert len(match.reasons) == 2

    def test_match_result_score_range(self):
        """测试匹配分数范围"""
        from src.domain.models.worker_profile import (
            ProfileMatchResult,
            WorkerProfile,
            ProfileType,
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
        )

        # 有效分数
        for score in [0.0, 0.5, 1.0]:
            match = ProfileMatchResult(
                profile=profile,
                score=score,
                matched_fields=[],
                reasons=[],
            )
            assert match.score == score

    def test_match_result_score_out_of_range(self):
        """测试分数超出范围"""
        from src.domain.models.worker_profile import (
            ProfileMatchResult,
            WorkerProfile,
            ProfileType,
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
        )

        # 分数超过 1
        with pytest.raises(ValidationError):
            ProfileMatchResult(
                profile=profile,
                score=1.5,
                matched_fields=[],
                reasons=[],
            )

        # 分数小于 0
        with pytest.raises(ValidationError):
            ProfileMatchResult(
                profile=profile,
                score=-0.1,
                matched_fields=[],
                reasons=[],
            )


class TestProfileSearchResult:
    """测试 ProfileSearchResult 模型"""

    def test_create_search_result_success(self):
        """测试创建搜索结果"""
        from src.domain.models.worker_profile import (
            ProfileSearchResult,
            ProfileMatchResult,
            WorkerProfile,
            ProfileType,
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
        )

        match = ProfileMatchResult(
            profile=profile,
            score=0.9,
            matched_fields=["skill.search"],
            reasons=["Skill matches query"],
        )

        result = ProfileSearchResult(
            matches=[match],
            query="search capability",
            total_count=1,
        )

        assert len(result.matches) == 1
        assert result.query == "search capability"
        assert result.total_count == 1

    def test_search_result_empty(self):
        """测试空搜索结果"""
        from src.domain.models.worker_profile import ProfileSearchResult

        result = ProfileSearchResult(
            matches=[],
            query="nonexistent",
            total_count=0,
        )

        assert len(result.matches) == 0
        assert result.total_count == 0


class TestProfileRecommendResult:
    """测试 ProfileRecommendResult 模型"""

    def test_create_recommend_result_success(self):
        """测试创建推荐结果"""
        from src.domain.models.worker_profile import (
            ProfileRecommendResult,
            ProfileMatchResult,
            WorkerProfile,
            ProfileType,
        )

        profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data",
        )

        match = ProfileMatchResult(
            profile=profile,
            score=0.95,
            matched_fields=["skill.code_review"],
            reasons=["Best match for code review task"],
        )

        result = ProfileRecommendResult(
            recommendations=[match],
            context="Need someone for code review",
            strategy="baseline",
        )

        assert len(result.recommendations) == 1
        assert result.context == "Need someone for code review"
        assert result.strategy == "baseline"

    def test_recommend_result_empty(self):
        """测试空推荐结果"""
        from src.domain.models.worker_profile import ProfileRecommendResult

        result = ProfileRecommendResult(
            recommendations=[],
            context="No suitable candidate",
            strategy="baseline",
        )

        assert len(result.recommendations) == 0