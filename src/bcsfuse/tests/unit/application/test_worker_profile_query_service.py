"""
Tests for Worker Profile Query Service

Worker Profile Ingestion Baseline

测试范围：
- WorkerProfileQueryService: 查询服务
- list_profiles()
- get_profile()
- get_profiles_by_staff()
- search_profiles()
- recommend_profiles()
"""

from __future__ import annotations

import json
import os
import tempfile
import pytest


class TestWorkerProfileQueryService:
    """测试 WorkerProfileQueryService"""

    @pytest.fixture
    def sample_data(self):
        """创建测试数据"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # staff_001: search skill
            staff1_path = os.path.join(tmpdir, "staff_001", "default", "openclaw")
            os.makedirs(staff1_path)
            with open(os.path.join(staff1_path, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Search Bot\nCapabilities:\n- Web Search\n")
            skills_path = os.path.join(staff1_path, "skills")
            os.makedirs(skills_path)
            with open(os.path.join(skills_path, "skill_sets.json"), "w") as f:
                json.dump({
                    "skill_sets": [{
                        "name": "search_skills",
                        "is_current": True,
                        "skills": [
                            {"name": "web_search", "description": "Search the web", "skill": "search_v1"},
                        ]
                    }]
                }, f)

            # staff_002: code skill
            staff2_path = os.path.join(tmpdir, "staff_002", "default", "openclaw")
            os.makedirs(staff2_path)
            with open(os.path.join(staff2_path, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Code Bot\nCapabilities:\n- Code Review\n")
            skills_path = os.path.join(staff2_path, "skills")
            os.makedirs(skills_path)
            with open(os.path.join(skills_path, "skill_sets.json"), "w") as f:
                json.dump({
                    "skill_sets": [{
                        "name": "code_skills",
                        "is_current": True,
                        "skills": [
                            {"name": "code_review", "description": "Review code", "skill": "review_v1"},
                        ]
                    }]
                }, f)

            # staff_003: both search and code
            staff3_path = os.path.join(tmpdir, "staff_003", "default", "openclaw")
            os.makedirs(staff3_path)
            with open(os.path.join(staff3_path, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Full Stack Bot\nCapabilities:\n- Web Search\n- Code Review\n")
            skills_path = os.path.join(staff3_path, "skills")
            os.makedirs(skills_path)
            with open(os.path.join(skills_path, "skill_sets.json"), "w") as f:
                json.dump({
                    "skill_sets": [{
                        "name": "full_skills",
                        "is_current": True,
                        "skills": [
                            {"name": "web_search", "description": "Search the web", "skill": "search_v1"},
                            {"name": "code_review", "description": "Review code", "skill": "review_v1"},
                        ]
                    }]
                }, f)

            yield tmpdir

    def test_list_profiles(self, sample_data):
        """测试 list_profiles 返回所有 profiles"""
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileQueryService(source)

        profiles = service.list_profiles()

        assert len(profiles) == 3
        staff_ids = {p.staff_id for p in profiles}
        assert staff_ids == {"001", "002", "003"}

    def test_get_profile_found(self, sample_data):
        """测试 get_profile 找到 profile"""
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileQueryService(source)

        profile = service.get_profile("001", "default")

        assert profile is not None
        assert profile.staff_id == "001"
        assert profile.profile_id == "default"

    def test_get_profile_not_found(self, sample_data):
        """测试 get_profile 找不到 profile"""
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileQueryService(source)

        profile = service.get_profile("999", "nonexistent")

        assert profile is None

    def test_get_profiles_by_staff(self, sample_data):
        """测试 get_profiles_by_staff"""
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileQueryService(source)

        profiles = service.get_profiles_by_staff("001")

        assert len(profiles) == 1
        assert profiles[0].staff_id == "001"


class TestWorkerProfileQueryServiceSearch:
    """测试搜索功能"""

    @pytest.fixture
    def sample_data(self):
        """创建测试数据"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # staff_001: search specialist
            staff1_path = os.path.join(tmpdir, "staff_001", "default", "openclaw")
            os.makedirs(staff1_path)
            with open(os.path.join(staff1_path, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Search Bot\n\nI specialize in web search.\n")
            skills_path = os.path.join(staff1_path, "skills")
            os.makedirs(skills_path)
            with open(os.path.join(skills_path, "skill_sets.json"), "w") as f:
                json.dump({
                    "skill_sets": [{
                        "name": "search_skills",
                        "is_current": True,
                        "skills": [
                            {"name": "web_search", "description": "Search the web for information", "skill": "v1"},
                        ]
                    }]
                }, f)

            # staff_002: code specialist
            staff2_path = os.path.join(tmpdir, "staff_002", "default", "openclaw")
            os.makedirs(staff2_path)
            with open(os.path.join(staff2_path, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Code Bot\n\nI specialize in code review.\n")
            skills_path = os.path.join(staff2_path, "skills")
            os.makedirs(skills_path)
            with open(os.path.join(skills_path, "skill_sets.json"), "w") as f:
                json.dump({
                    "skill_sets": [{
                        "name": "code_skills",
                        "is_current": True,
                        "skills": [
                            {"name": "code_review", "description": "Review source code", "skill": "v1"},
                        ]
                    }]
                }, f)

            yield tmpdir

    def test_search_profiles_by_query(self, sample_data):
        """测试 search_profiles 返回带 score 和 reasons 的结果"""
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileQueryService(source)

        result = service.search_profiles("search")

        # 返回 ProfileSearchResult
        from src.domain.models.worker_profile import ProfileSearchResult
        assert isinstance(result, ProfileSearchResult)

        # 查询被保存
        assert result.query == "search"

        # 结果按分数排序
        assert result.total_count > 0

        # 检查匹配结果
        for match in result.matches:
            assert match.score >= 0
            assert match.score <= 1
            assert isinstance(match.reasons, list)

    def test_search_profiles_empty_result(self, sample_data):
        """测试搜索无结果"""
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileQueryService(source)

        result = service.search_profiles("nonexistent_keyword_xyz")

        assert result.total_count == 0
        assert len(result.matches) == 0


class TestWorkerProfileQueryServiceRecommend:
    """测试推荐功能"""

    @pytest.fixture
    def sample_data(self):
        """创建测试数据"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # staff_001: search specialist
            staff1_path = os.path.join(tmpdir, "staff_001", "default", "openclaw")
            os.makedirs(staff1_path)
            with open(os.path.join(staff1_path, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Search Expert\n\nI am an expert in searching.\n")
            skills_path = os.path.join(staff1_path, "skills")
            os.makedirs(skills_path)
            with open(os.path.join(skills_path, "skill_sets.json"), "w") as f:
                json.dump({
                    "skill_sets": [{
                        "name": "search_skills",
                        "is_current": True,
                        "skills": [
                            {"name": "web_search", "description": "Search the web", "skill": "v1"},
                        ]
                    }]
                }, f)

            # staff_002: code specialist
            staff2_path = os.path.join(tmpdir, "staff_002", "default", "openclaw")
            os.makedirs(staff2_path)
            with open(os.path.join(staff2_path, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Code Expert\n\nI am an expert in coding.\n")
            skills_path = os.path.join(staff2_path, "skills")
            os.makedirs(skills_path)
            with open(os.path.join(skills_path, "skill_sets.json"), "w") as f:
                json.dump({
                    "skill_sets": [{
                        "name": "code_skills",
                        "is_current": True,
                        "skills": [
                            {"name": "code_review", "description": "Review code", "skill": "v1"},
                        ]
                    }]
                }, f)

            yield tmpdir

    def test_recommend_profiles(self, sample_data):
        """测试 recommend_profiles 返回带 score 和 reasons 的结果"""
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileQueryService(source)

        result = service.recommend_profiles("I need help with web search")

        # 返回 ProfileRecommendResult
        from src.domain.models.worker_profile import ProfileRecommendResult
        assert isinstance(result, ProfileRecommendResult)

        # context 被保存
        assert result.context == "I need help with web search"

        # 策略是 baseline
        assert result.strategy == "baseline"

        # 检查推荐结果
        for rec in result.recommendations:
            assert rec.score >= 0
            assert rec.score <= 1
            assert isinstance(rec.reasons, list)
            assert isinstance(rec.matched_fields, list)

    def test_recommend_profiles_empty(self, sample_data):
        """测试推荐无结果"""
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = WorkerProfileSettings(roots=[tmpdir])
            source = FileWorkerProfileSource(settings)
            service = WorkerProfileQueryService(source)

            result = service.recommend_profiles("need help")

            assert len(result.recommendations) == 0
            assert result.strategy == "baseline"


class TestWorkerProfileQueryServiceTopK:
    """测试 top_k 参数"""

    @pytest.fixture
    def sample_data(self):
        """创建多个 profile 的测试数据"""
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(5):
                staff_path = os.path.join(tmpdir, f"staff_{i:03d}", "default", "openclaw")
                os.makedirs(staff_path)
                with open(os.path.join(staff_path, "SOUL.md"), "w") as f:
                    f.write(f"# Identity\nName: Bot {i}\nSearch capability.\n")
                skills_path = os.path.join(staff_path, "skills")
                os.makedirs(skills_path)
                with open(os.path.join(skills_path, "skill_sets.json"), "w") as f:
                    json.dump({
                        "skill_sets": [{
                            "name": f"skills_{i}",
                            "is_current": True,
                            "skills": [{"name": f"skill_{i}", "skill": "v1"}]
                        }]
                    }, f)

            yield tmpdir

    def test_search_top_k(self, sample_data):
        """测试搜索 top_k"""
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileQueryService(source)

        result = service.search_profiles("search", top_k=3)

        assert len(result.matches) <= 3

    def test_recommend_top_k(self, sample_data):
        """测试推荐 top_k"""
        from src.application.services.worker_profile_query_service import (
            WorkerProfileQueryService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileQueryService(source)

        result = service.recommend_profiles("need search", top_k=2)

        assert len(result.recommendations) <= 2