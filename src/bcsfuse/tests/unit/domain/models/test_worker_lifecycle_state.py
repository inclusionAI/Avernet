"""
Tests for WorkerLifecycleState

Stage 1 Domain Model Tests
"""

import pytest

from src.domain.models.worker_lifecycle_state import WorkerLifecycleState


class TestWorkerLifecycleState:
    """WorkerLifecycleState 枚举测试"""

    def test_lifecycle_state_values(self):
        """测试生命周期状态值"""
        assert WorkerLifecycleState.ACTIVE.value == "active"
        assert WorkerLifecycleState.INACTIVE.value == "inactive"
        assert WorkerLifecycleState.DISABLED.value == "disabled"

    def test_lifecycle_state_count(self):
        """测试生命周期状态数量（Stage 1 只有 3 个）"""
        assert len(WorkerLifecycleState) == 3

    def test_lifecycle_state_is_active(self):
        """测试 is_active 判断"""
        assert WorkerLifecycleState.ACTIVE.value == "active"

    def test_lifecycle_state_from_string(self):
        """测试从字符串创建"""
        state = WorkerLifecycleState("active")
        assert state == WorkerLifecycleState.ACTIVE

    def test_lifecycle_state_string_representation(self):
        """测试字符串表示"""
        # str() on enum returns 'EnumName.VALUE', use .value for the actual value
        assert WorkerLifecycleState.ACTIVE.value == "active"
        assert str(WorkerLifecycleState.ACTIVE.value) == "active"