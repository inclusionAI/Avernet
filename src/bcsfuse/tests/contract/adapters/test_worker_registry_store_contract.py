"""
Contract Tests for WorkerRegistryStoreAdapter

验证所有实现（InMemory / SQLite）都符合 WorkerRegistryStoreAdapter 协议。
"""

import pytest

from tests.contract.adapters.helpers import (
    WorkerRegistryStoreContractTests,
    make_sample_worker,
)
from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
from src.application.ports.worker_profile_content_store import WorkerProfileContentStore
from src.application.ports.worker_registry_store import WorkerRegistryStore
from src.bootstrap.profile_store_compat import (
    WorkerProfileContentStoreCompatibilityAdapter,
)
from src.domain.models.worker_profile_content import WorkerProfileContent
from src.infra.public.stores.in_memory_worker_profile_content_store import (
    InMemoryWorkerProfileContentStore,
)


def test_public_worker_store_ports_match_domain_services():
    registry_store = InMemoryWorkerRegistryStore()
    profile_store = InMemoryWorkerProfileContentStore()

    assert isinstance(registry_store, WorkerRegistryStore)
    assert isinstance(profile_store, WorkerProfileContentStore)

    saved = profile_store.save(
        WorkerProfileContent(
            worker_id="wrk_profile_port",
            profile_id="default",
            display_name="Port Contract",
        )
    )
    assert profile_store.get(saved.worker_id, saved.profile_id) == saved
    assert profile_store.list_by_worker(saved.worker_id).items == [saved]


def test_in_memory_profile_activation_keeps_only_one_active_profile():
    store = InMemoryWorkerProfileContentStore()
    first = store.save(
        WorkerProfileContent(
            worker_id="wrk_profile_activation",
            profile_id="first",
            display_name="First",
        )
    )
    second = store.save(
        WorkerProfileContent(
            worker_id=first.worker_id,
            profile_id="second",
            display_name="Second",
        )
    )

    store.activate(first.worker_id, first.profile_id)
    activated = store.activate(second.worker_id, second.profile_id)

    assert activated is not None
    assert activated.is_active is True
    assert store.get(first.worker_id, first.profile_id).is_active is False
    assert store.get_active(first.worker_id).profile_id == second.profile_id


def test_profile_route_compatibility_adapter_uses_only_typed_port():
    typed_store = InMemoryWorkerProfileContentStore()
    adapter = WorkerProfileContentStoreCompatibilityAdapter(typed_store)
    worker_id = "wrk_profile_compat"
    profile_id = "default"

    assert adapter.upsert_profile(
        worker_id,
        profile_id,
        {
            "worker_id": worker_id,
            "profile_id": profile_id,
            "display_name": "Compatibility",
        },
    )
    assert adapter.get_profile(worker_id, profile_id)["display_name"] == "Compatibility"
    assert adapter.list_profiles(worker_id)[0]["profile_id"] == profile_id
    assert adapter.activate_profile(worker_id, profile_id)
    assert adapter.get_active_profile_for_worker(worker_id)["profile_id"] == profile_id
    assert adapter.get_active_profiles([worker_id])[0]["worker_id"] == worker_id
    assert adapter.delete_profile(worker_id, profile_id)
    assert adapter.get_profile(worker_id, profile_id) is None


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

    def test_delete_failure_propagates_and_rolls_back(self, sqlite_registry_store):
        """A failed cascade must not report success or partially delete state."""
        worker_id = "wrk_delete_rollback"
        sqlite_registry_store.create(make_sample_worker(worker_id))
        sqlite_registry_store._conn.execute(
            """
            CREATE TABLE bcsfuse_worker_profile_contents (
                worker_id TEXT NOT NULL
            )
            """
        )
        sqlite_registry_store._conn.execute(
            "INSERT INTO bcsfuse_worker_profile_contents(worker_id) VALUES (?)",
            (worker_id,),
        )
        sqlite_registry_store._conn.execute(
            """
            CREATE TRIGGER reject_worker_delete
            BEFORE DELETE ON bcsfuse_workers
            BEGIN
                SELECT RAISE(ABORT, 'worker delete rejected');
            END
            """
        )
        sqlite_registry_store._conn.commit()

        with pytest.raises(Exception, match="worker delete rejected"):
            sqlite_registry_store.delete(worker_id)

        assert sqlite_registry_store.exists(worker_id)
        remaining = sqlite_registry_store._conn.execute(
            "SELECT COUNT(*) FROM bcsfuse_worker_profile_contents WHERE worker_id = ?",
            (worker_id,),
        ).fetchone()[0]
        assert remaining == 1

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
