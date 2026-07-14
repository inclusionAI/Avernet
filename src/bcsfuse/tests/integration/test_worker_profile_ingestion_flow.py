"""
Integration Test for Worker Profile Ingestion Flow

Worker Profile Ingestion Baseline

测试完整的摄取流程，使用 fixture 数据验证端到端行为。
"""

from __future__ import annotations

import os
import pytest


class TestWorkerProfileIngestionFlow:
    """测试完整的摄取流程"""

    @pytest.fixture
    def fixture_root(self):
        """获取 fixture 根目录"""
        return os.path.join(
            os.path.dirname(__file__),
            "..",
            "fixtures",
            "worker_profile_source",
            "bolt_data",
        )

    def test_full_ingestion_flow(self, fixture_root):
        """测试完整摄取流程"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.application.services.worker_profile_ingestion_service import (
            WorkerProfileIngestionService,
        )
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )

        # 1. 配置
        settings = WorkerProfileSettings(roots=[fixture_root])

        # 2. 创建 source
        source = FileWorkerProfileSource(settings)

        # 3. 创建服务
        ingestion_service = WorkerProfileIngestionService(source)
        query_service = WorkerProfileQueryService(source)

        # 4. 执行摄取
        result = ingestion_service.ingest()

        # 5. 验证结果
        # 应该有 3 个 profiles:
        # - staff_001: default
        # - staff_260065: default
        # - staff_260065: bot (20260319_qjmzo9k6)
        assert len(result.profiles) == 3
        assert len(result.source_roots) == 1

        # 验证 source_root
        assert fixture_root in result.source_roots

    def test_profile_content_integrity(self, fixture_root):
        """测试 profile 内容完整性"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.domain.models.context_fragment import ContextKind
        from src.domain.models.worker_profile import ProfileType

        settings = WorkerProfileSettings(roots=[fixture_root])
        source = FileWorkerProfileSource(settings)

        # 获取 staff_260065 的 default profile
        profile = source.get_profile("260065", "default")

        assert profile is not None
        assert profile.staff_id == "260065"
        assert profile.profile_id == "default"
        assert profile.profile_type == ProfileType.DEFAULT

        # 验证上下文
        soul_fragment = next(
            (f for f in profile.context_fragments if f.kind == ContextKind.SOUL),
            None
        )
        assert soul_fragment is not None
        assert "Default Worker" in soul_fragment.content

        # 验证技能
        assert len(profile.active_skills) == 2
        skill_names = {s.name for s in profile.active_skills}
        assert "web_search" in skill_names
        assert "data_analysis" in skill_names

        # 验证 searchable_text 已生成
        assert len(profile.searchable_text) > 0
        assert "[CONTEXT:soul:" in profile.searchable_text
        assert "[SKILL:" in profile.searchable_text

    def test_bot_profile_content(self, fixture_root):
        """测试 bot profile 内容"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.domain.models.worker_profile import ProfileType

        settings = WorkerProfileSettings(roots=[fixture_root])
        source = FileWorkerProfileSource(settings)

        # 获取 bot profile
        profile = source.get_profile("260065", "20260319_qjmzo9k6")

        assert profile is not None
        assert profile.profile_type == ProfileType.BOT
        assert profile.profile_id == "20260319_qjmzo9k6"

        # 验证技能
        assert len(profile.active_skills) == 1
        assert profile.active_skills[0].name == "deep_research"

    def test_query_service_search(self, fixture_root):
        """测试查询服务搜索功能"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )

        settings = WorkerProfileSettings(roots=[fixture_root])
        source = FileWorkerProfileSource(settings)
        query_service = WorkerProfileQueryService(source)

        # 搜索 "code"
        result = query_service.search_profiles("code")

        # 应该返回 staff_001 (Code Review Bot)
        assert result.total_count >= 1

        # 搜索 "research"
        result = query_service.search_profiles("research")

        # 应该返回 staff_260065 的 bot (Research Bot)
        assert result.total_count >= 1

    def test_query_service_recommend(self, fixture_root):
        """测试查询服务推荐功能"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )

        settings = WorkerProfileSettings(roots=[fixture_root])
        source = FileWorkerProfileSource(settings)
        query_service = WorkerProfileQueryService(source)

        # 推荐 for code review task
        result = query_service.recommend_profiles("I need help with code review")

        assert result.strategy == "baseline"
        assert len(result.recommendations) >= 1

        # 第一个推荐应该是 code review 相关的
        if result.recommendations:
            top_rec = result.recommendations[0]
            assert top_rec.score > 0
            assert len(top_rec.reasons) > 0 or len(top_rec.matched_fields) > 0

    def test_multiple_context_files(self, fixture_root):
        """测试多个上下文文件"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.domain.models.context_fragment import ContextKind

        settings = WorkerProfileSettings(roots=[fixture_root])
        source = FileWorkerProfileSource(settings)

        # staff_001 有 SOUL.md 和 AGENTS.md
        profile = source.get_profile("001", "default")

        assert profile is not None
        assert len(profile.context_fragments) >= 2

        kinds = {f.kind for f in profile.context_fragments}
        assert ContextKind.SOUL in kinds
        assert ContextKind.AGENT in kinds


class TestWorkerProfileSettingsIntegration:
    """测试配置集成"""

    def test_custom_file_mapping(self):
        """测试自定义文件映射"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.domain.models.context_fragment import ContextKind

        custom_mapping = {
            "SOUL.md": ContextKind.SOUL,
            "MYCUSTOM.md": ContextKind.OTHER,
        }

        settings = WorkerProfileSettings(
            roots=["/nonexistent"],
            context_file_mapping=custom_mapping,
        )

        assert settings.get_context_kind("MYCUSTOM.md") == ContextKind.OTHER
        # 未配置的文件返回 OTHER
        assert settings.get_context_kind("UNKNOWN.md") == ContextKind.OTHER

    def test_backup_directory_handling(self):
        """测试备份目录处理"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        # 默认不包含备份
        settings = WorkerProfileSettings()
        assert settings.is_backup_directory("default_bak") is True
        assert settings.include_backup is False

        # 设置包含备份
        settings = WorkerProfileSettings(include_backup=True)
        assert settings.include_backup is True


class TestTotalWarningsCount:
    """测试总警告数计算"""

    @pytest.fixture
    def fixture_root(self):
        """获取 fixture 根目录"""
        return os.path.join(
            os.path.dirname(__file__),
            "..",
            "fixtures",
            "worker_profile_source",
            "bolt_data",
        )

    def test_total_warnings_across_profiles(self, fixture_root):
        """测试跨 profile 警告总数"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[fixture_root])
        source = FileWorkerProfileSource(settings)
        result = source.scan()

        # total_warnings 应该正确计算
        expected = len(result.scan_warnings) + sum(
            len(p.warnings) for p in result.profiles
        )
        assert result.total_warnings == expected


class TestSearchableTextFormat:
    """测试 searchable_text 格式"""

    @pytest.fixture
    def fixture_root(self):
        """获取 fixture 根目录"""
        return os.path.join(
            os.path.dirname(__file__),
            "..",
            "fixtures",
            "worker_profile_source",
            "bolt_data",
        )

    def test_searchable_text_format(self, fixture_root):
        """测试 searchable_text 固定格式"""
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        import re

        settings = WorkerProfileSettings(roots=[fixture_root])
        source = FileWorkerProfileSource(settings)

        profile = source.get_profile("260065", "default")
        assert profile is not None

        text = profile.searchable_text

        # 验证格式正确
        # 应以 [CONTEXT: 开头
        assert text.startswith("[CONTEXT:")

        # 应包含 [SKILL: 段
        assert "[SKILL:" in text

        # 上下文应该在技能前面
        context_pos = text.find("[CONTEXT:")
        skill_pos = text.find("[SKILL:")
        assert context_pos < skill_pos

        # 验证格式一致性
        context_pattern = r"\[CONTEXT:\w+:[^\]]*\]"
        skill_pattern = r"\[SKILL:[^\]]*:[^\]]*\]"

        context_matches = re.findall(context_pattern, text)
        skill_matches = re.findall(skill_pattern, text)

        # 应该有匹配
        assert len(context_matches) > 0
        assert len(skill_matches) > 0