"""
Tests for InMemory Worker Registry Store

Stage 1 Adapter Tests
"""

import pytest

from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
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
from src.domain.models.worker_source_info import WorkerSourceType
from src.domain.exceptions import DuplicateWorkerException, WorkerNotFoundException


class TestInMemoryWorkerRegistryStore:
    """InMemoryWorkerRegistryStore 测试"""

    @pytest.fixture
    def store(self):
        """创建 store 实例"""
        return InMemoryWorkerRegistryStore()

    @pytest.fixture
    def sample_worker(self):
        """创建示例 Worker"""
        return Worker(
            id="wrk_test_001",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Test Bot", handle="@test-bot"),
            responsibilities=["testing"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.TRUSTED,
            ),
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            source_type=WorkerSourceType.API,
        )

    def test_create_worker(self, store, sample_worker):
        """测试创建 Worker"""
        created = store.create(sample_worker)

        assert created.id == sample_worker.id
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.version == 1

    def test_create_duplicate_worker(self, store, sample_worker):
        """测试创建重复 Worker"""
        store.create(sample_worker)

        with pytest.raises(DuplicateWorkerException):
            store.create(sample_worker)

    def test_get_by_id(self, store, sample_worker):
        """测试通过 ID 获取"""
        store.create(sample_worker)

        found = store.get_by_id("wrk_test_001")
        assert found is not None
        assert found.id == "wrk_test_001"

    def test_get_by_id_not_found(self, store):
        """测试获取不存在的 Worker"""
        found = store.get_by_id("wrk_not_exist")
        assert found is None

    def test_list_all(self, store, sample_worker):
        """测试列出所有"""
        store.create(sample_worker)

        workers = store.list()
        assert len(workers) == 1

    def test_list_filter_by_lifecycle_state(self, store, sample_worker):
        """测试按生命周期状态过滤"""
        store.create(sample_worker)

        # 创建另一个 inactive worker
        inactive_worker = Worker(
            id="wrk_test_002",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Inactive Bot", handle="@inactive-bot"),
            responsibilities=["testing"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.TRUSTED,
            ),
            lifecycle_state=WorkerLifecycleState.INACTIVE,
        )
        store.create(inactive_worker)

        # 过滤
        active_workers = store.list(
            lifecycle_states=[WorkerLifecycleState.ACTIVE]
        )
        assert len(active_workers) == 1
        assert active_workers[0].id == "wrk_test_001"

        inactive_workers = store.list(
            lifecycle_states=[WorkerLifecycleState.INACTIVE]
        )
        assert len(inactive_workers) == 1
        assert inactive_workers[0].id == "wrk_test_002"

    def test_list_filter_by_source_type(self, store, sample_worker):
        """测试按来源类型过滤"""
        store.create(sample_worker)

        # 创建 FILE 来源的 worker
        file_worker = Worker(
            id="wrk_test_002",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="File Bot", handle="@file-bot"),
            responsibilities=["testing"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.TRUSTED,
            ),
            source_type=WorkerSourceType.FILE,
        )
        store.create(file_worker)

        # 过滤
        api_workers = store.list(source_types=[WorkerSourceType.API])
        assert len(api_workers) == 1

        file_workers = store.list(source_types=[WorkerSourceType.FILE])
        assert len(file_workers) == 1

    def test_list_filter_by_domains(self, store, sample_worker):
        """测试按领域过滤"""
        sample_worker.domains = ["ai", "ml"]
        store.create(sample_worker)

        workers = store.list(domains=["ai"])
        assert len(workers) == 1

        workers = store.list(domains=["backend"])
        assert len(workers) == 0

    def test_list_pagination(self, store):
        """测试分页"""
        # 创建 5 个 workers
        for i in range(5):
            worker = Worker(
                id=f"wrk_test_{i:03d}",
                type=WorkerType.BOT,
                identity=WorkerIdentity(name=f"Bot {i}", handle=f"@bot-{i}"),
                responsibilities=["testing"],
                capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
                state=WorkerState(
                    availability=Availability.AVAILABLE,
                    trust_level=TrustLevel.TRUSTED,
                ),
            )
            store.create(worker)

        # 测试分页
        page1 = store.list(limit=2, offset=0)
        assert len(page1) == 2

        page2 = store.list(limit=2, offset=2)
        assert len(page2) == 2

    def test_update_worker(self, store, sample_worker):
        """测试更新 Worker"""
        created = store.create(sample_worker)

        # 更新
        created.identity.name = "Updated Bot"
        updated = store.update(created)

        assert updated.identity.name == "Updated Bot"
        assert updated.version == 2

    def test_update_worker_version_conflict(self, store, sample_worker):
        """测试版本冲突"""
        created = store.create(sample_worker)

        # 修改版本号
        created.version = 999

        with pytest.raises(ValueError):
            store.update(created)

    def test_update_lifecycle_state(self, store, sample_worker):
        """测试更新生命周期状态"""
        created = store.create(sample_worker)

        # 更新状态
        updated = store.update_lifecycle_state(
            worker_id="wrk_test_001",
            lifecycle_state=WorkerLifecycleState.INACTIVE,
            version=created.version,
        )

        assert updated.lifecycle_state == WorkerLifecycleState.INACTIVE
        assert updated.version == created.version + 1

    def test_delete_worker(self, store, sample_worker):
        """测试删除 Worker"""
        store.create(sample_worker)

        result = store.delete("wrk_test_001")
        assert result is True
        assert not store.exists("wrk_test_001")

    def test_delete_worker_not_found(self, store):
        """测试删除不存在的 Worker"""
        with pytest.raises(WorkerNotFoundException):
            store.delete("wrk_not_exist")

    def test_exists(self, store, sample_worker):
        """测试存在检查"""
        assert not store.exists("wrk_test_001")

        store.create(sample_worker)
        assert store.exists("wrk_test_001")

    def test_count(self, store, sample_worker):
        """测试计数"""
        assert store.count() == 0

        store.create(sample_worker)
        assert store.count() == 1

    def test_count_by_lifecycle_state(self, store, sample_worker):
        """测试按生命周期状态计数"""
        store.create(sample_worker)

        # 创建 inactive worker
        inactive_worker = Worker(
            id="wrk_test_002",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Inactive Bot", handle="@inactive-bot"),
            responsibilities=["testing"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.TRUSTED,
            ),
            lifecycle_state=WorkerLifecycleState.INACTIVE,
        )
        store.create(inactive_worker)

        assert store.count(lifecycle_states=[WorkerLifecycleState.ACTIVE]) == 1
        assert store.count(lifecycle_states=[WorkerLifecycleState.INACTIVE]) == 1

    def test_clear(self, store, sample_worker):
        """测试清空"""
        store.create(sample_worker)
        assert store.count() == 1

        store.clear()
        assert store.count() == 0