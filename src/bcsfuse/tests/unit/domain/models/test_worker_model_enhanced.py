"""
Tests for Worker Model Enhanced

Stage 1: 测试 Worker 模型新增字段
"""

import pytest
from datetime import datetime

from src.domain.models.worker import (
    Worker,
    WorkerType,
    WorkerIdentity,
    WorkerState,
    Availability,
    TrustLevel,
    Capability,
    CapabilityLevel,
)
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.models.worker_source_info import WorkerSourceType


class TestWorkerModelEnhanced:
    """Worker 模型增强测试"""

    @pytest.fixture
    def base_worker_data(self):
        """基础 worker 数据"""
        return {
            "id": "wrk_test_001",
            "type": WorkerType.BOT,
            "identity": WorkerIdentity(
                name="Test Bot",
                handle="@test-bot",
            ),
            "responsibilities": ["testing"],
            "capabilities": [
                Capability(name="test", level=CapabilityLevel.EXPERT)
            ],
            "state": WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.TRUSTED,
            ),
        }

    def test_worker_with_lifecycle_state(self, base_worker_data):
        """测试 Worker 生命周期状态"""
        worker = Worker(**base_worker_data)

        # 默认值
        assert worker.lifecycle_state == WorkerLifecycleState.ACTIVE

        # 指定值
        base_worker_data["lifecycle_state"] = WorkerLifecycleState.INACTIVE
        worker = Worker(**base_worker_data)
        assert worker.lifecycle_state == WorkerLifecycleState.INACTIVE

    def test_worker_with_runtime_state(self, base_worker_data):
        """测试 Worker 运行态"""
        worker = Worker(**base_worker_data)

        # 默认 runtime_state
        assert worker.state.runtime_state == WorkerRuntimeState.OFFLINE

        # 指定 runtime_state
        base_worker_data["state"] = WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            runtime_state=WorkerRuntimeState.ONLINE,
        )
        worker = Worker(**base_worker_data)
        assert worker.state.runtime_state == WorkerRuntimeState.ONLINE

    def test_worker_with_source_info(self, base_worker_data):
        """测试 Worker 来源信息"""
        worker = Worker(**base_worker_data)

        # 默认值
        assert worker.source_type == WorkerSourceType.API
        assert worker.source_ref is None
        assert worker.external_id is None

        # 指定值
        base_worker_data["source_type"] = WorkerSourceType.FILE
        base_worker_data["source_ref"] = "/data/profiles"
        base_worker_data["external_id"] = "ext_123"
        worker = Worker(**base_worker_data)
        assert worker.source_type == WorkerSourceType.FILE
        assert worker.source_ref == "/data/profiles"
        assert worker.external_id == "ext_123"

    def test_worker_with_management_metadata(self, base_worker_data):
        """测试 Worker 管理元数据"""
        worker = Worker(**base_worker_data)

        # 自动生成
        assert worker.created_at is not None
        assert worker.updated_at is not None
        assert worker.version == 1

        # 可选字段
        base_worker_data["created_by"] = "user_001"
        base_worker_data["updated_by"] = "user_002"
        worker = Worker(**base_worker_data)
        assert worker.created_by == "user_001"
        assert worker.updated_by == "user_002"

    def test_worker_with_active_profile_key(self, base_worker_data):
        """测试 Worker 活跃 profile 引用"""
        worker = Worker(**base_worker_data)

        # 默认为 None
        assert worker.active_profile_key is None

        # 指定值
        base_worker_data["active_profile_key"] = "staff_123:default"
        worker = Worker(**base_worker_data)
        assert worker.active_profile_key == "staff_123:default"

    def test_worker_version_increment(self, base_worker_data):
        """测试 Worker 版本增量"""
        worker = Worker(**base_worker_data)
        assert worker.version == 1

        # 更新版本
        worker.version = 2
        assert worker.version == 2

    def test_worker_default_values(self, base_worker_data):
        """测试 Worker 默认值"""
        worker = Worker(**base_worker_data)

        assert worker.lifecycle_state == WorkerLifecycleState.ACTIVE
        assert worker.source_type == WorkerSourceType.API
        assert worker.version == 1
        assert worker.state.runtime_state == WorkerRuntimeState.OFFLINE

    def test_worker_full_creation(self, base_worker_data):
        """测试完整创建 Worker"""
        base_worker_data.update({
            "lifecycle_state": WorkerLifecycleState.ACTIVE,
            "source_type": WorkerSourceType.API,
            "source_ref": "api://upstream",
            "external_id": "ext_456",
            "active_profile_key": "staff_789:default",
            "created_by": "admin",
        })

        worker = Worker(**base_worker_data)

        assert worker.id == "wrk_test_001"
        assert worker.lifecycle_state == WorkerLifecycleState.ACTIVE
        assert worker.source_type == WorkerSourceType.API
        assert worker.source_ref == "api://upstream"
        assert worker.external_id == "ext_456"
        assert worker.active_profile_key == "staff_789:default"
        assert worker.created_by == "admin"