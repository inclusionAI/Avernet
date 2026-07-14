"""
Contract Tests for WorkerProfileBindingStoreAdapter

验证所有实现（InMemory / SQLite）都符合 WorkerProfileBindingStoreAdapter 协议。
"""

import pytest

from tests.contract.adapters.helpers import WorkerProfileBindingStoreContractTests
from src.infra.adapters.in_memory_worker_profile_binding_store import InMemoryWorkerProfileBindingStore


# ============================================================================
# InMemory Implementation Tests
# ============================================================================

@pytest.fixture
def in_memory_profile_binding_store():
    """创建 InMemory WorkerProfileBindingStore 实例"""
    return InMemoryWorkerProfileBindingStore()


class TestInMemoryWorkerProfileBindingStore:
    """InMemory 实现的契约测试"""

    def test_bind_profile(self, in_memory_profile_binding_store):
        """测试绑定 Profile"""
        WorkerProfileBindingStoreContractTests(in_memory_profile_binding_store).test_bind_profile()

    def test_get_active_binding(self, in_memory_profile_binding_store):
        """测试获取活跃绑定"""
        WorkerProfileBindingStoreContractTests(in_memory_profile_binding_store).test_get_active_binding()

    def test_get_active_binding_not_found(self, in_memory_profile_binding_store):
        """测试获取不存在的活跃绑定"""
        WorkerProfileBindingStoreContractTests(in_memory_profile_binding_store).test_get_active_binding_not_found()

    def test_only_one_active_profile_per_worker(self, in_memory_profile_binding_store):
        """测试一个 Worker 只能有一个 active profile"""
        WorkerProfileBindingStoreContractTests(in_memory_profile_binding_store).test_only_one_active_profile_per_worker()

    def test_set_active_profile(self, in_memory_profile_binding_store):
        """测试设置活跃 Profile"""
        WorkerProfileBindingStoreContractTests(in_memory_profile_binding_store).test_set_active_profile()

    def test_unbind_profile(self, in_memory_profile_binding_store):
        """测试解绑 Profile"""
        WorkerProfileBindingStoreContractTests(in_memory_profile_binding_store).test_unbind_profile()

    def test_list_bindings_by_worker(self, in_memory_profile_binding_store):
        """测试列出 Worker 的所有绑定"""
        WorkerProfileBindingStoreContractTests(in_memory_profile_binding_store).test_list_bindings_by_worker()


# ============================================================================
# SQLite Implementation Tests
# ============================================================================

@pytest.fixture
def sqlite_profile_binding_store():
    """创建 SQLite WorkerProfileBindingStore 实例"""
    from src.infra.adapters.sqlite_worker_profile_binding_store import SQLiteWorkerProfileBindingStore
    return SQLiteWorkerProfileBindingStore(":memory:")


class TestSQLiteWorkerProfileBindingStore:
    """SQLite 实现的契约测试"""

    def test_bind_profile(self, sqlite_profile_binding_store):
        """测试绑定 Profile"""
        WorkerProfileBindingStoreContractTests(sqlite_profile_binding_store).test_bind_profile()

    def test_get_active_binding(self, sqlite_profile_binding_store):
        """测试获取活跃绑定"""
        WorkerProfileBindingStoreContractTests(sqlite_profile_binding_store).test_get_active_binding()

    def test_get_active_binding_not_found(self, sqlite_profile_binding_store):
        """测试获取不存在的活跃绑定"""
        WorkerProfileBindingStoreContractTests(sqlite_profile_binding_store).test_get_active_binding_not_found()

    def test_only_one_active_profile_per_worker(self, sqlite_profile_binding_store):
        """测试一个 Worker 只能有一个 active profile"""
        WorkerProfileBindingStoreContractTests(sqlite_profile_binding_store).test_only_one_active_profile_per_worker()

    def test_set_active_profile(self, sqlite_profile_binding_store):
        """测试设置活跃 Profile"""
        WorkerProfileBindingStoreContractTests(sqlite_profile_binding_store).test_set_active_profile()

    def test_unbind_profile(self, sqlite_profile_binding_store):
        """测试解绑 Profile"""
        WorkerProfileBindingStoreContractTests(sqlite_profile_binding_store).test_unbind_profile()

    def test_list_bindings_by_worker(self, sqlite_profile_binding_store):
        """测试列出 Worker 的所有绑定"""
        WorkerProfileBindingStoreContractTests(sqlite_profile_binding_store).test_list_bindings_by_worker()