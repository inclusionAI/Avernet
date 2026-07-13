"""
Contract Tests for WorkerRuntimeStateStoreAdapter

验证所有实现（InMemory / SQLite）都符合 WorkerRuntimeStateStoreAdapter 协议。
"""

import pytest

from tests.contract.adapters.helpers import WorkerRuntimeStateStoreContractTests
from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore


# ============================================================================
# InMemory Implementation Tests
# ============================================================================

@pytest.fixture
def in_memory_runtime_state_store():
    """创建 InMemory WorkerRuntimeStateStore 实例"""
    return InMemoryWorkerRuntimeStateStore()


class TestInMemoryWorkerRuntimeStateStore:
    """InMemory 实现的契约测试"""

    def test_get_runtime_state_not_found(self, in_memory_runtime_state_store):
        """测试获取不存在的运行态"""
        WorkerRuntimeStateStoreContractTests(in_memory_runtime_state_store).test_get_runtime_state_not_found()

    def test_set_and_get_runtime_state(self, in_memory_runtime_state_store):
        """测试设置和获取运行态"""
        WorkerRuntimeStateStoreContractTests(in_memory_runtime_state_store).test_set_and_get_runtime_state()

    def test_set_runtime_state_with_updated_by(self, in_memory_runtime_state_store):
        """测试带更新者设置运行态"""
        WorkerRuntimeStateStoreContractTests(in_memory_runtime_state_store).test_set_runtime_state_with_updated_by()

    def test_batch_get_runtime_states(self, in_memory_runtime_state_store):
        """测试批量获取运行态"""
        WorkerRuntimeStateStoreContractTests(in_memory_runtime_state_store).test_batch_get_runtime_states()

    def test_count_by_state(self, in_memory_runtime_state_store):
        """测试按状态计数"""
        WorkerRuntimeStateStoreContractTests(in_memory_runtime_state_store).test_count_by_state()


# ============================================================================
# SQLite Implementation Tests
# ============================================================================

@pytest.fixture
def sqlite_runtime_state_store():
    """创建 SQLite WorkerRuntimeStateStore 实例"""
    from src.infra.adapters.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore
    return SQLiteWorkerRuntimeStateStore(":memory:")


class TestSQLiteWorkerRuntimeStateStore:
    """SQLite 实现的契约测试"""

    def test_get_runtime_state_not_found(self, sqlite_runtime_state_store):
        """测试获取不存在的运行态"""
        WorkerRuntimeStateStoreContractTests(sqlite_runtime_state_store).test_get_runtime_state_not_found()

    def test_set_and_get_runtime_state(self, sqlite_runtime_state_store):
        """测试设置和获取运行态"""
        WorkerRuntimeStateStoreContractTests(sqlite_runtime_state_store).test_set_and_get_runtime_state()

    def test_set_runtime_state_with_updated_by(self, sqlite_runtime_state_store):
        """测试带更新者设置运行态"""
        WorkerRuntimeStateStoreContractTests(sqlite_runtime_state_store).test_set_runtime_state_with_updated_by()

    def test_batch_get_runtime_states(self, sqlite_runtime_state_store):
        """测试批量获取运行态"""
        WorkerRuntimeStateStoreContractTests(sqlite_runtime_state_store).test_batch_get_runtime_states()

    def test_count_by_state(self, sqlite_runtime_state_store):
        """测试按状态计数"""
        WorkerRuntimeStateStoreContractTests(sqlite_runtime_state_store).test_count_by_state()