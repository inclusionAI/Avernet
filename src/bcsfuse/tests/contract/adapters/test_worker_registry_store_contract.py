"""
Contract Tests for WorkerRegistryStoreAdapter

验证所有实现（InMemory / SQLite）都符合 WorkerRegistryStoreAdapter 协议。
"""

import pytest

from tests.contract.adapters.helpers import WorkerRegistryStoreContractTests
from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore


# ============================================================================
# InMemory Implementation Tests
# ============================================================================

@pytest.fixture
def in_memory_registry_store():
    """创建 InMemory WorkerRegistryStore 实例"""
    return InMemoryWorkerRegistryStore()


class TestInMemoryWorkerRegistryStore:
    """InMemory 实现的契约测试"""

    def test_create(self, in_memory_registry_store):
        """测试创建 Worker"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_create()

    def test_create_duplicate_raises_error(self, in_memory_registry_store):
        """测试创建重复 Worker 报错"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_create_duplicate_raises_error()

    def test_get_by_id_not_found(self, in_memory_registry_store):
        """测试获取不存在的 Worker"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_get_by_id_not_found()

    def test_list_all(self, in_memory_registry_store):
        """测试列出所有"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_list_all()

    def test_list_filter_by_lifecycle_state(self, in_memory_registry_store):
        """测试按生命周期状态过滤"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_list_filter_by_lifecycle_state()

    def test_list_filter_by_source_type(self, in_memory_registry_store):
        """测试按来源类型过滤"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_list_filter_by_source_type()

    def test_list_filter_by_domains(self, in_memory_registry_store):
        """测试按领域过滤"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_list_filter_by_domains()

    def test_list_pagination(self, in_memory_registry_store):
        """测试分页"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_list_pagination()

    def test_update(self, in_memory_registry_store):
        """测试更新 Worker"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_update()

    def test_update_version_conflict(self, in_memory_registry_store):
        """测试版本冲突"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_update_version_conflict()

    def test_update_lifecycle_state(self, in_memory_registry_store):
        """测试更新生命周期状态"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_update_lifecycle_state()

    def test_delete(self, in_memory_registry_store):
        """测试删除 Worker"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_delete()

    def test_delete_not_found(self, in_memory_registry_store):
        """测试删除不存在的 Worker"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_delete_not_found()

    def test_exists(self, in_memory_registry_store):
        """测试存在检查"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_exists()

    def test_count(self, in_memory_registry_store):
        """测试计数"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_count()

    def test_count_by_lifecycle_state(self, in_memory_registry_store):
        """测试按生命周期状态计数"""
        WorkerRegistryStoreContractTests(in_memory_registry_store).test_count_by_lifecycle_state()


# ============================================================================
# SQLite Implementation Tests
# ============================================================================

@pytest.fixture
def sqlite_registry_store():
    """创建 SQLite WorkerRegistryStore 实例"""
    from src.infra.adapters.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
    return SQLiteWorkerRegistryStore(":memory:")


class TestSQLiteWorkerRegistryStore:
    """SQLite 实现的契约测试"""

    def test_create(self, sqlite_registry_store):
        """测试创建 Worker"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_create()

    def test_create_duplicate_raises_error(self, sqlite_registry_store):
        """测试创建重复 Worker 报错"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_create_duplicate_raises_error()

    def test_get_by_id_not_found(self, sqlite_registry_store):
        """测试获取不存在的 Worker"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_get_by_id_not_found()

    def test_list_all(self, sqlite_registry_store):
        """测试列出所有"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_list_all()

    def test_list_filter_by_lifecycle_state(self, sqlite_registry_store):
        """测试按生命周期状态过滤"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_list_filter_by_lifecycle_state()

    def test_list_filter_by_source_type(self, sqlite_registry_store):
        """测试按来源类型过滤"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_list_filter_by_source_type()

    def test_list_filter_by_domains(self, sqlite_registry_store):
        """测试按领域过滤"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_list_filter_by_domains()

    def test_list_pagination(self, sqlite_registry_store):
        """测试分页"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_list_pagination()

    def test_update(self, sqlite_registry_store):
        """测试更新 Worker"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_update()

    def test_update_version_conflict(self, sqlite_registry_store):
        """测试版本冲突"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_update_version_conflict()

    def test_update_lifecycle_state(self, sqlite_registry_store):
        """测试更新生命周期状态"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_update_lifecycle_state()

    def test_delete(self, sqlite_registry_store):
        """测试删除 Worker"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_delete()

    def test_delete_not_found(self, sqlite_registry_store):
        """测试删除不存在的 Worker"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_delete_not_found()

    def test_exists(self, sqlite_registry_store):
        """测试存在检查"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_exists()

    def test_count(self, sqlite_registry_store):
        """测试计数"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_count()

    def test_count_by_lifecycle_state(self, sqlite_registry_store):
        """测试按生命周期状态计数"""
        WorkerRegistryStoreContractTests(sqlite_registry_store).test_count_by_lifecycle_state()