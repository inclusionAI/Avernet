"""
Tests for WorkerSourceInfo

Stage 1 Domain Model Tests
"""

import pytest
from datetime import datetime

from src.domain.models.worker_source_info import WorkerSourceType, WorkerSourceInfo


class TestWorkerSourceType:
    """WorkerSourceType 枚举测试"""

    def test_source_type_values(self):
        """测试来源类型值"""
        assert WorkerSourceType.API.value == "api"
        assert WorkerSourceType.FILE.value == "file"
        assert WorkerSourceType.IMPORT.value == "import"

    def test_source_type_count(self):
        """测试来源类型数量"""
        assert len(WorkerSourceType) == 3

    def test_source_type_from_string(self):
        """测试从字符串创建"""
        source = WorkerSourceType("api")
        assert source == WorkerSourceType.API


class TestWorkerSourceInfo:
    """WorkerSourceInfo 模型测试"""

    def test_source_info_creation_default(self):
        """测试默认创建"""
        source_info = WorkerSourceInfo()
        assert source_info.source_type == WorkerSourceType.API
        assert source_info.source_ref is None
        assert source_info.external_id is None
        assert source_info.imported_at is not None

    def test_source_info_creation_with_values(self):
        """测试带值创建"""
        source_info = WorkerSourceInfo(
            source_type=WorkerSourceType.FILE,
            source_ref="/data/profiles",
            external_id="ext_123",
        )
        assert source_info.source_type == WorkerSourceType.FILE
        assert source_info.source_ref == "/data/profiles"
        assert source_info.external_id == "ext_123"

    def test_source_info_forbid_extra_fields(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):
            WorkerSourceInfo(
                source_type=WorkerSourceType.API,
                unknown_field="value",
            )

    def test_source_info_imported_at_auto(self):
        """测试 imported_at 自动设置"""
        before = datetime.utcnow()
        source_info = WorkerSourceInfo()
        after = datetime.utcnow()

        assert before <= source_info.imported_at <= after