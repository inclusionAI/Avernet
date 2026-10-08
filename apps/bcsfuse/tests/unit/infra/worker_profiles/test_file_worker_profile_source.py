"""
Tests for File Worker Profile Source

Worker Profile Ingestion Baseline

测试范围：
- FileWorkerProfileSource: 文件来源实现
- 协调 scanner、context loader、skill loader
- scan()、get_profile()、get_profiles_by_staff() 方法
"""

from __future__ import annotations

import json
import os
import tempfile
import pytest


class TestFileWorkerProfileSource:
    """测试 FileWorkerProfileSource"""

    @pytest.fixture
    def sample_data(self):
        """创建测试数据"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 staff_001 的 default profile
            staff1_default = os.path.join(tmpdir, "staff_001", "default", "openclaw")
            os.makedirs(staff1_default)

            # SOUL.md
            with open(os.path.join(staff1_default, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Test Bot 001\n")

            # skills
            skills_path = os.path.join(staff1_default, "skills")
            os.makedirs(skills_path)
            with open(os.path.join(skills_path, "skill_sets.json"), "w") as f:
                json.dump({
                    "skill_sets": [
                        {
                            "name": "default_skills",
                            "is_current": True,
                            "skills": [
                                {"name": "search", "description": "Search capability", "skill": "search_v1"},
                            ],
                        }
                    ]
                }, f)

            # 创建 staff_002 的 default 和 bot
            staff2_default = os.path.join(tmpdir, "staff_002", "default", "openclaw")
            os.makedirs(staff2_default)
            with open(os.path.join(staff2_default, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Staff 002 Default\n")

            staff2_bot = os.path.join(tmpdir, "staff_002", "20260319_test0001", "openclaw")
            os.makedirs(staff2_bot)
            with open(os.path.join(staff2_bot, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Staff 002 Bot\n")

            yield tmpdir

    def test_scan_returns_all_profiles(self, sample_data):
        """测试 scan 返回所有 profiles"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        result = source.scan()

        # 应该有 3 个 profiles
        assert len(result.profiles) == 3

        # 验证 scan_warnings
        assert isinstance(result.scan_warnings, list)

        # 验证 source_roots
        assert sample_data in result.source_roots

    def test_get_profile_found(self, sample_data):
        """测试 get_profile 找到 profile"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)

        profile = source.get_profile("001", "default")

        assert profile is not None
        assert profile.staff_id == "001"
        assert profile.profile_id == "default"
        assert profile.profile_type.value == "default"

    def test_get_profile_not_found(self, sample_data):
        """测试 get_profile 找不到 profile"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)

        profile = source.get_profile("999", "nonexistent")

        assert profile is None

    def test_get_profiles_by_staff(self, sample_data):
        """测试 get_profiles_by_staff 返回员工所有 profiles"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)

        # staff_002 有 2 个 profiles
        profiles = source.get_profiles_by_staff("002")

        assert len(profiles) == 2
        profile_ids = {p.profile_id for p in profiles}
        assert "default" in profile_ids
        assert "20260319_test0001" in profile_ids

        # staff_001 只有 1 个 profile
        profiles = source.get_profiles_by_staff("001")
        assert len(profiles) == 1

    def test_profile_has_context_fragments(self, sample_data):
        """测试 profile 包含上下文片段"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.domain.models.context_fragment import ContextKind

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)

        profile = source.get_profile("001", "default")

        assert profile is not None
        assert len(profile.context_fragments) > 0

        # 找到 SOUL.md
        soul_fragment = next(
            (f for f in profile.context_fragments if f.kind == ContextKind.SOUL),
            None
        )
        assert soul_fragment is not None
        assert "Test Bot 001" in soul_fragment.content

    def test_profile_has_active_skills(self, sample_data):
        """测试 profile 包含激活技能"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)

        profile = source.get_profile("001", "default")

        assert profile is not None
        assert len(profile.active_skills) > 0

        skill = profile.active_skills[0]
        assert skill.name == "search"
        assert skill.description == "Search capability"
        assert skill.is_active is True

    def test_profile_has_searchable_text(self, sample_data):
        """测试 profile 包含可检索文本"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)

        profile = source.get_profile("001", "default")

        assert profile is not None
        assert len(profile.searchable_text) > 0

        # 检查格式
        assert "[CONTEXT:" in profile.searchable_text
        assert "[SKILL:" in profile.searchable_text


class TestFileWorkerProfileSourceEdgeCases:
    """测试 FileWorkerProfileSource 边缘情况"""

    def test_scan_empty_root(self):
        """测试扫描空根目录"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = WorkerProfileSettings(roots=[tmpdir])
            source = FileWorkerProfileSource(settings)
            result = source.scan()

            assert len(result.profiles) == 0

    def test_scan_missing_openclaw(self):
        """测试缺少 openclaw 目录"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 staff 目录但没有 openclaw
            staff_path = os.path.join(tmpdir, "staff_001", "default")
            os.makedirs(staff_path)

            settings = WorkerProfileSettings(roots=[tmpdir])
            source = FileWorkerProfileSource(settings)
            result = source.scan()

            # 没有 openclaw，profile 不应该被包含
            assert len(result.profiles) == 0
            # 应该有警告
            assert len(result.scan_warnings) > 0

    def test_scan_missing_skill_sets(self):
        """测试缺少 skill_sets.json"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建完整的目录结构但没有 skills
            staff_path = os.path.join(tmpdir, "staff_001", "default", "openclaw")
            os.makedirs(staff_path)
            with open(os.path.join(staff_path, "SOUL.md"), "w") as f:
                f.write("# Test")

            settings = WorkerProfileSettings(roots=[tmpdir])
            source = FileWorkerProfileSource(settings)
            result = source.scan()

            # profile 应该被加载
            assert len(result.profiles) == 1
            profile = result.profiles[0]

            # 没有激活技能
            assert len(profile.active_skills) == 0

            # 应该有警告在 profile 的 warnings 中
            assert len(profile.warnings) > 0
            assert any("skill" in w.code.lower() for w in profile.warnings)


class TestFileWorkerProfileSourceProtocol:
    """测试 FileWorkerProfileSource 实现 WorkerProfileSource 协议"""

    def test_implements_protocol(self):
        """测试实现 WorkerProfileSource 协议"""
        from src.domain.services.worker_profile_source import WorkerProfileSource
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        # 检查是否实现协议
        source = FileWorkerProfileSource()
        assert isinstance(source, WorkerProfileSource)

    def test_protocol_methods_exist(self):
        """测试协议方法存在"""
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        source = FileWorkerProfileSource()

        # 检查方法存在
        assert hasattr(source, "scan")
        assert hasattr(source, "get_profile")
        assert hasattr(source, "get_profiles_by_staff")
        assert callable(source.scan)
        assert callable(source.get_profile)
        assert callable(source.get_profiles_by_staff)