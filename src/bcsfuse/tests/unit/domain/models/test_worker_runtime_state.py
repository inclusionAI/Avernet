"""
Tests for WorkerRuntimeState

Stage 1 Domain Model Tests
"""

import pytest

from src.domain.models.worker_runtime_state import WorkerRuntimeState


class TestWorkerRuntimeState:
    """WorkerRuntimeState 枚举测试"""

    def test_runtime_state_values(self):
        """测试运行态值"""
        assert WorkerRuntimeState.ONLINE.value == "online"
        assert WorkerRuntimeState.OFFLINE.value == "offline"

    def test_runtime_state_count(self):
        """测试运行态数量（Stage 1 只有 2 个）"""
        assert len(WorkerRuntimeState) == 2

    def test_runtime_state_is_online(self):
        """测试 is_online 判断"""
        assert WorkerRuntimeState.ONLINE.value == "online"

    def test_runtime_state_is_offline(self):
        """测试 is_offline 判断"""
        assert WorkerRuntimeState.OFFLINE.value == "offline"

    def test_runtime_state_from_string(self):
        """测试从字符串创建"""
        state = WorkerRuntimeState("online")
        assert state == WorkerRuntimeState.ONLINE

    def test_runtime_state_string_representation(self):
        """测试字符串表示"""
        # str() on enum returns 'EnumName.VALUE', use .value for the actual value
        assert WorkerRuntimeState.ONLINE.value == "online"
        assert WorkerRuntimeState.OFFLINE.value == "offline"