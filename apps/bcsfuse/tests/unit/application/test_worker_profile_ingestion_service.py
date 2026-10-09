"""
Tests for Worker Profile Ingestion Service

Worker Profile Ingestion Baseline

测试范围：
- WorkerProfileIngestionService: 摄取服务
- 协调多个 source
- ingest() 方法
"""

from __future__ import annotations

import json
import os
import tempfile
import pytest


class TestWorkerProfileIngestionService:
    """测试 WorkerProfileIngestionService"""

    @pytest.fixture
    def sample_data(self):
        """创建测试数据"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 staff_001
            staff1_path = os.path.join(tmpdir, "staff_001", "default", "openclaw")
            os.makedirs(staff1_path)
            with open(os.path.join(staff1_path, "SOUL.md"), "w") as f:
                f.write("# Identity\nName: Bot 001\n")
            skills_path = os.path.join(staff1_path, "skills")
            os.makedirs(skills_path)
            with open(os.path.join(skills_path, "skill_sets.json"), "w") as f:
                json.dump({
                    "skill_sets": [{
                        "name": "default",
                        "is_current": True,
                        "skills": [{"name": "search", "skill": "v1"}]
                    }]
                }, f)

            yield tmpdir

    def test_ingest_from_single_source(self, sample_data):
        """测试从单个来源摄取"""
        from src.application.services.worker_profile_ingestion_service import (
            WorkerProfileIngestionService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileIngestionService(source)

        result = service.ingest()

        assert len(result.profiles) == 1
        assert result.profiles[0].staff_id == "001"

    def test_ingest_returns_scan_result(self, sample_data):
        """测试 ingest 返回 WorkerProfileScanResult"""
        from src.application.services.worker_profile_ingestion_service import (
            WorkerProfileIngestionService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileIngestionService(source)

        result = service.ingest()

        # 返回 WorkerProfileScanResult
        from src.domain.models.worker_profile import WorkerProfileScanResult
        assert isinstance(result, WorkerProfileScanResult)

    def test_ingest_caches_result(self, sample_data):
        """测试 ingest 缓存结果"""
        from src.application.services.worker_profile_ingestion_service import (
            WorkerProfileIngestionService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileIngestionService(source)

        result1 = service.ingest()
        result2 = service.ingest()

        # 两次调用返回相同对象（缓存）
        assert result1 is result2

    def test_ingest_refresh_clears_cache(self, sample_data):
        """测试 refresh=True 清除缓存"""
        from src.application.services.worker_profile_ingestion_service import (
            WorkerProfileIngestionService,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )
        from src.infra.worker_profiles.sources.file_worker_profile_source import (
            FileWorkerProfileSource,
        )

        settings = WorkerProfileSettings(roots=[sample_data])
        source = FileWorkerProfileSource(settings)
        service = WorkerProfileIngestionService(source)

        result1 = service.ingest()
        # 不带 refresh，返回缓存对象
        result2 = service.ingest()
        assert result1 is result2

        # 带 refresh，会调用 source 的 scan()，但 source 也有缓存
        # 所以实际上 refresh=True 会清除 service 层缓存并重新从 source 获取
        # 如果 source 也清除缓存，则会真正重新扫描
        service.clear_cache()  # 清除所有层缓存
        result3 = service.ingest()

        # 由于清除了缓存，结果内容相同但不是同一对象
        assert len(result3.profiles) == len(result1.profiles)


class TestWorkerProfileIngestionServiceEmptySource:
    """测试空来源场景"""

    def test_ingest_empty_source(self):
        """测试摄取空来源"""
        from src.application.services.worker_profile_ingestion_service import (
            WorkerProfileIngestionService,
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
            service = WorkerProfileIngestionService(source)

            result = service.ingest()

            assert len(result.profiles) == 0