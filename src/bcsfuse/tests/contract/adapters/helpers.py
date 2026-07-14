"""
Contract Tests Helpers

提供可复用的测试工厂方法，让 InMemory 和 SQLite 共享同一套契约测试。

核心设计：
- 每个 adapter 提供 make_* 方法生成测试所需的方法
- 测试数据统一管理
- 断言辅助函数
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

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
from src.domain.models.worker_audit_log import WorkerAuditLog, WorkerAuditAction
from src.domain.models.worker_profile_binding import WorkerProfileBinding

if TYPE_CHECKING:
    from src.domain.services.adapters.worker_registry_store_adapter import WorkerRegistryStoreAdapter
    from src.domain.services.adapters.worker_runtime_state_store_adapter import WorkerRuntimeStateStoreAdapter
    from src.domain.services.adapters.worker_profile_binding_store_adapter import WorkerProfileBindingStoreAdapter
    from src.domain.services.adapters.worker_audit_log_adapter import WorkerAuditLogAdapter


# ============================================================================
# Test Data Factories
# ============================================================================

def make_sample_worker(
    worker_id: str = "wrk_test_001",
    name: str = "Test Worker",
    handle: str = "@test-worker",
    lifecycle_state: WorkerLifecycleState = WorkerLifecycleState.ACTIVE,
    source_type: WorkerSourceType = WorkerSourceType.API,
    domains: Optional[list[str]] = None,
) -> Worker:
    """
    创建示例 Worker

    Args:
        worker_id: Worker ID
        name: 名称
        handle: handle
        lifecycle_state: 生命周期状态
        source_type: 来源类型
        domains: 领域列表

    Returns:
        Worker 实例
    """
    return Worker(
        id=worker_id,
        type=WorkerType.BOT,
        identity=WorkerIdentity(name=name, handle=handle),
        responsibilities=["testing"],
        capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
        state=WorkerState(
            availability=Availability.PRIVATE,
            trust_level=TrustLevel.TRUSTED,
        ),
        lifecycle_state=lifecycle_state,
        source_type=source_type,
        domains=domains or [],
    )


def make_sample_audit_log(
    worker_id: str = "wrk_test_001",
    action: WorkerAuditAction = WorkerAuditAction.CREATED,
    performed_by: Optional[str] = None,
) -> WorkerAuditLog:
    """
    创建示例审计日志

    Args:
        worker_id: Worker ID
        action: 审计动作
        performed_by: 执行者

    Returns:
        WorkerAuditLog 实例
    """
    return WorkerAuditLog(
        worker_id=worker_id,
        action=action,
        performed_by=performed_by,
    )


def make_sample_profile_binding(
    worker_id: str = "wrk_test_001",
    profile_key: str = "staff_001:default",
    source_type: WorkerSourceType = WorkerSourceType.FILE,
) -> WorkerProfileBinding:
    """
    创建示例 Profile 绑定

    Args:
        worker_id: Worker ID
        profile_key: Profile Key
        source_type: 来源类型

    Returns:
        WorkerProfileBinding 实例
    """
    return WorkerProfileBinding(
        worker_id=worker_id,
        profile_key=profile_key,
        source_type=source_type,
    )


# ============================================================================
# Contract Test Runners
# ============================================================================

class WorkerRegistryStoreContractTests:
    """
    WorkerRegistryStoreAdapter 契约测试

    使用方式：
        def test_create(in_memory_registry_store):
            WorkerRegistryStoreContractTests(in_memory_registry_store).test_create()

        def test_create(sqlite_registry_store):
            WorkerRegistryStoreContractTests(sqlite_registry_store).test_create()
    """

    def __init__(self, store: "WorkerRegistryStoreAdapter"):
        self.store = store

    def test_create(self) -> Worker:
        """测试创建 Worker"""
        worker = make_sample_worker()
        created = self.store.create(worker)

        assert created.id == worker.id
        assert created.created_at is not None
        assert created.updated_at is not None
        assert created.version == 1

        # 验证可以读取
        found = self.store.get_by_id(worker.id)
        assert found is not None
        assert found.id == worker.id

        return created

    def test_create_duplicate_raises_error(self) -> None:
        """测试创建重复 Worker 报错"""
        from src.domain.exceptions import DuplicateWorkerException

        worker = make_sample_worker()
        self.store.create(worker)

        import pytest
        with pytest.raises(DuplicateWorkerException):
            self.store.create(worker)

    def test_get_by_id_not_found(self) -> None:
        """测试获取不存在的 Worker"""
        found = self.store.get_by_id("wrk_not_exist")
        assert found is None

    def test_list_all(self) -> None:
        """测试列出所有"""
        worker1 = make_sample_worker("wrk_list_001")
        worker2 = make_sample_worker("wrk_list_002")
        self.store.create(worker1)
        self.store.create(worker2)

        workers = self.store.list()
        assert len(workers) >= 2

    def test_list_filter_by_lifecycle_state(self) -> None:
        """测试按生命周期状态过滤"""
        active_worker = make_sample_worker(
            "wrk_active_001",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
        )
        inactive_worker = make_sample_worker(
            "wrk_inactive_001",
            lifecycle_state=WorkerLifecycleState.INACTIVE,
        )
        self.store.create(active_worker)
        self.store.create(inactive_worker)

        active_workers = self.store.list(
            lifecycle_states=[WorkerLifecycleState.ACTIVE]
        )
        assert all(w.lifecycle_state == WorkerLifecycleState.ACTIVE for w in active_workers)

        inactive_workers = self.store.list(
            lifecycle_states=[WorkerLifecycleState.INACTIVE]
        )
        assert all(w.lifecycle_state == WorkerLifecycleState.INACTIVE for w in inactive_workers)

    def test_list_filter_by_source_type(self) -> None:
        """测试按来源类型过滤"""
        api_worker = make_sample_worker(
            "wrk_api_001",
            source_type=WorkerSourceType.API,
        )
        file_worker = make_sample_worker(
            "wrk_file_001",
            source_type=WorkerSourceType.FILE,
        )
        self.store.create(api_worker)
        self.store.create(file_worker)

        api_workers = self.store.list(source_types=[WorkerSourceType.API])
        assert all(w.source_type == WorkerSourceType.API for w in api_workers)

        file_workers = self.store.list(source_types=[WorkerSourceType.FILE])
        assert all(w.source_type == WorkerSourceType.FILE for w in file_workers)

    def test_list_filter_by_domains(self) -> None:
        """测试按领域过滤"""
        worker1 = make_sample_worker("wrk_domain_001", domains=["ai", "ml"])
        worker2 = make_sample_worker("wrk_domain_002", domains=["backend"])
        self.store.create(worker1)
        self.store.create(worker2)

        ai_workers = self.store.list(domains=["ai"])
        assert any(w.id == "wrk_domain_001" for w in ai_workers)
        assert not any(w.id == "wrk_domain_002" for w in ai_workers)

    def test_list_pagination(self) -> None:
        """测试分页"""
        # 创建多个 workers
        for i in range(5):
            worker = make_sample_worker(f"wrk_page_{i:03d}")
            self.store.create(worker)

        page1 = self.store.list(limit=2, offset=0)
        assert len(page1) == 2

        page2 = self.store.list(limit=2, offset=2)
        assert len(page2) == 2

    def test_update(self) -> None:
        """测试更新 Worker"""
        worker = make_sample_worker("wrk_update_001")
        created = self.store.create(worker)

        # 更新
        created.identity.name = "Updated Worker"
        updated = self.store.update(created)

        assert updated.identity.name == "Updated Worker"
        assert updated.version == created.version + 1

        # 验证持久化
        found = self.store.get_by_id("wrk_update_001")
        assert found is not None
        assert found.identity.name == "Updated Worker"
        assert found.version == 2

    def test_update_version_conflict(self) -> None:
        """测试版本冲突"""
        import pytest

        worker = make_sample_worker("wrk_version_001")
        created = self.store.create(worker)

        # 修改版本号
        created.version = 999

        with pytest.raises(ValueError):
            self.store.update(created)

    def test_update_lifecycle_state(self) -> None:
        """测试更新生命周期状态"""
        worker = make_sample_worker("wrk_lifecycle_001")
        created = self.store.create(worker)

        updated = self.store.update_lifecycle_state(
            worker_id="wrk_lifecycle_001",
            lifecycle_state=WorkerLifecycleState.INACTIVE,
            version=created.version,
        )

        assert updated.lifecycle_state == WorkerLifecycleState.INACTIVE
        assert updated.version == created.version + 1

    def test_delete(self) -> None:
        """测试删除 Worker"""
        worker = make_sample_worker("wrk_delete_001")
        self.store.create(worker)

        result = self.store.delete("wrk_delete_001")
        assert result is True
        assert not self.store.exists("wrk_delete_001")

    def test_delete_not_found(self) -> None:
        """测试删除不存在的 Worker"""
        from src.domain.exceptions import WorkerNotFoundException
        import pytest

        with pytest.raises(WorkerNotFoundException):
            self.store.delete("wrk_not_exist")

    def test_exists(self) -> None:
        """测试存在检查"""
        assert not self.store.exists("wrk_exist_001")

        worker = make_sample_worker("wrk_exist_001")
        self.store.create(worker)

        assert self.store.exists("wrk_exist_001")

    def test_count(self) -> None:
        """测试计数"""
        initial_count = self.store.count()

        worker = make_sample_worker("wrk_count_001")
        self.store.create(worker)

        assert self.store.count() == initial_count + 1

    def test_count_by_lifecycle_state(self) -> None:
        """测试按生命周期状态计数"""
        active_worker = make_sample_worker(
            "wrk_count_active_001",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
        )
        inactive_worker = make_sample_worker(
            "wrk_count_inactive_001",
            lifecycle_state=WorkerLifecycleState.INACTIVE,
        )
        self.store.create(active_worker)
        self.store.create(inactive_worker)

        active_count = self.store.count(lifecycle_states=[WorkerLifecycleState.ACTIVE])
        inactive_count = self.store.count(lifecycle_states=[WorkerLifecycleState.INACTIVE])

        assert active_count >= 1
        assert inactive_count >= 1


class WorkerRuntimeStateStoreContractTests:
    """
    WorkerRuntimeStateStoreAdapter 契约测试
    """

    def __init__(self, store: "WorkerRuntimeStateStoreAdapter"):
        self.store = store

    def test_get_runtime_state_not_found(self) -> None:
        """测试获取不存在的运行态"""
        state = self.store.get_runtime_state("wrk_not_exist")
        assert state is None

    def test_set_and_get_runtime_state(self) -> None:
        """测试设置和获取运行态"""
        worker_id = "wrk_runtime_001"

        # 设置 online
        result = self.store.set_runtime_state(worker_id, WorkerRuntimeState.ONLINE)
        assert result is True

        # 获取
        state = self.store.get_runtime_state(worker_id)
        assert state == WorkerRuntimeState.ONLINE

        # 设置 offline
        self.store.set_runtime_state(worker_id, WorkerRuntimeState.OFFLINE)
        state = self.store.get_runtime_state(worker_id)
        assert state == WorkerRuntimeState.OFFLINE

    def test_set_runtime_state_with_updated_by(self) -> None:
        """测试带更新者设置运行态"""
        worker_id = "wrk_runtime_002"

        self.store.set_runtime_state(
            worker_id,
            WorkerRuntimeState.ONLINE,
            updated_by="admin",
        )

        state = self.store.get_runtime_state(worker_id)
        assert state == WorkerRuntimeState.ONLINE

    def test_batch_get_runtime_states(self) -> None:
        """测试批量获取运行态"""
        # 设置多个 worker 状态
        self.store.set_runtime_state("wrk_batch_001", WorkerRuntimeState.ONLINE)
        self.store.set_runtime_state("wrk_batch_002", WorkerRuntimeState.OFFLINE)
        # wrk_batch_003 不设置

        states = self.store.batch_get_runtime_states([
            "wrk_batch_001",
            "wrk_batch_002",
            "wrk_batch_003",
        ])

        assert states.get("wrk_batch_001") == WorkerRuntimeState.ONLINE
        assert states.get("wrk_batch_002") == WorkerRuntimeState.OFFLINE
        assert "wrk_batch_003" not in states or states.get("wrk_batch_003") is None

    def test_count_by_state(self) -> None:
        """测试按状态计数"""
        # 设置一些状态
        self.store.set_runtime_state("wrk_count_online_001", WorkerRuntimeState.ONLINE)
        self.store.set_runtime_state("wrk_count_online_002", WorkerRuntimeState.ONLINE)
        self.store.set_runtime_state("wrk_count_offline_001", WorkerRuntimeState.OFFLINE)

        online_count = self.store.count_by_state(WorkerRuntimeState.ONLINE)
        offline_count = self.store.count_by_state(WorkerRuntimeState.OFFLINE)

        assert online_count >= 2
        assert offline_count >= 1


class WorkerProfileBindingStoreContractTests:
    """
    WorkerProfileBindingStoreAdapter 契约测试
    """

    def __init__(self, store: "WorkerProfileBindingStoreAdapter"):
        self.store = store

    def test_bind_profile(self) -> None:
        """测试绑定 Profile"""
        binding = self.store.bind_profile(
            worker_id="wrk_bind_001",
            profile_key="staff_001:default",
            source_type=WorkerSourceType.FILE,
        )

        assert binding.worker_id == "wrk_bind_001"
        assert binding.profile_key == "staff_001:default"
        assert binding.is_active is True

    def test_get_active_binding(self) -> None:
        """测试获取活跃绑定"""
        self.store.bind_profile(
            worker_id="wrk_active_001",
            profile_key="staff_active:default",
            source_type=WorkerSourceType.FILE,
        )

        binding = self.store.get_active_binding("wrk_active_001")
        assert binding is not None
        assert binding.profile_key == "staff_active:default"

    def test_get_active_binding_not_found(self) -> None:
        """测试获取不存在的活跃绑定"""
        binding = self.store.get_active_binding("wrk_not_exist")
        assert binding is None

    def test_only_one_active_profile_per_worker(self) -> None:
        """
        测试一个 Worker 只能有一个 active profile

        这是 Stage 1 的核心规则
        """
        worker_id = "wrk_one_active_001"

        # 绑定第一个 profile
        binding1 = self.store.bind_profile(
            worker_id=worker_id,
            profile_key="profile_001",
            source_type=WorkerSourceType.FILE,
        )
        assert binding1.is_active is True

        # 绑定第二个 profile（应该替换第一个）
        binding2 = self.store.bind_profile(
            worker_id=worker_id,
            profile_key="profile_002",
            source_type=WorkerSourceType.FILE,
        )

        # 活跃绑定应该是第二个
        active = self.store.get_active_binding(worker_id)
        assert active is not None
        assert active.profile_key == "profile_002"

        # 列出所有绑定，应该只有一个活跃的
        bindings = self.store.list_bindings_by_worker(worker_id)
        active_bindings = [b for b in bindings if b.is_active]
        assert len(active_bindings) == 1

    def test_set_active_profile(self) -> None:
        """测试设置活跃 Profile"""
        worker_id = "wrk_set_active_001"

        # 先绑定一个
        self.store.bind_profile(
            worker_id=worker_id,
            profile_key="profile_old",
            source_type=WorkerSourceType.FILE,
        )

        # 设置新的活跃 profile
        result = self.store.set_active_profile(
            worker_id=worker_id,
            profile_key="profile_new",
        )
        assert result is True

        # 验证
        active = self.store.get_active_binding(worker_id)
        assert active is not None
        assert active.profile_key == "profile_new"

    def test_unbind_profile(self) -> None:
        """测试解绑 Profile"""
        worker_id = "wrk_unbind_001"
        profile_key = "staff_unbind:default"

        self.store.bind_profile(
            worker_id=worker_id,
            profile_key=profile_key,
            source_type=WorkerSourceType.FILE,
        )

        result = self.store.unbind_profile(worker_id, profile_key)
        assert result is True

        # 验证解绑后没有活跃绑定
        binding = self.store.get_active_binding(worker_id)
        assert binding is None

    def test_list_bindings_by_worker(self) -> None:
        """测试列出 Worker 的所有绑定"""
        worker_id = "wrk_list_bindings_001"

        self.store.bind_profile(
            worker_id=worker_id,
            profile_key="profile_list_001",
            source_type=WorkerSourceType.FILE,
        )

        bindings = self.store.list_bindings_by_worker(worker_id)
        assert len(bindings) >= 1
        assert any(b.profile_key == "profile_list_001" for b in bindings)


class WorkerAuditLogAdapterContractTests:
    """
    WorkerAuditLogAdapter 契约测试
    """

    def __init__(self, adapter: "WorkerAuditLogAdapter"):
        self.adapter = adapter

    def test_append_log(self) -> None:
        """测试追加日志"""
        log = make_sample_audit_log(
            worker_id="wrk_audit_001",
            action=WorkerAuditAction.CREATED,
        )

        self.adapter.append_log(log)

        # 验证可以查询到
        logs = self.adapter.list_logs(worker_id="wrk_audit_001")
        assert len(logs) >= 1
        assert any(l.worker_id == "wrk_audit_001" for l in logs)

    def test_list_logs_by_worker(self) -> None:
        """测试按 Worker ID 查询日志"""
        worker_id = "wrk_audit_list_001"

        # 创建多条日志
        for action in [WorkerAuditAction.CREATED, WorkerAuditAction.UPDATED]:
            log = make_sample_audit_log(worker_id=worker_id, action=action)
            self.adapter.append_log(log)

        logs = self.adapter.list_logs(worker_id=worker_id)
        assert len(logs) >= 2

    def test_list_logs_by_actions(self) -> None:
        """测试按动作类型过滤日志"""
        worker_id = "wrk_audit_filter_001"

        # 创建不同类型的日志
        self.adapter.append_log(make_sample_audit_log(
            worker_id=worker_id,
            action=WorkerAuditAction.CREATED,
        ))
        self.adapter.append_log(make_sample_audit_log(
            worker_id=worker_id,
            action=WorkerAuditAction.RUNTIME_STATE_CHANGED,
        ))

        created_logs = self.adapter.list_logs(
            worker_id=worker_id,
            actions=[WorkerAuditAction.CREATED],
        )
        assert all(l.action == WorkerAuditAction.CREATED for l in created_logs)

    def test_list_logs_pagination(self) -> None:
        """测试日志分页"""
        worker_id = "wrk_audit_page_001"

        # 创建多条日志
        for i in range(5):
            log = make_sample_audit_log(
                worker_id=worker_id,
                action=WorkerAuditAction.UPDATED,
            )
            self.adapter.append_log(log)

        page1 = self.adapter.list_logs(worker_id=worker_id, limit=2, offset=0)
        assert len(page1) == 2

        page2 = self.adapter.list_logs(worker_id=worker_id, limit=2, offset=2)
        assert len(page2) == 2

    def test_get_latest_log(self) -> None:
        """测试获取最新日志"""
        worker_id = "wrk_audit_latest_001"

        # 创建日志
        log1 = make_sample_audit_log(
            worker_id=worker_id,
            action=WorkerAuditAction.CREATED,
        )
        self.adapter.append_log(log1)

        log2 = make_sample_audit_log(
            worker_id=worker_id,
            action=WorkerAuditAction.RUNTIME_STATE_CHANGED,
        )
        self.adapter.append_log(log2)

        # 最新日志应该是最后追加的
        latest = self.adapter.get_latest_log(worker_id)
        assert latest is not None
        assert latest.worker_id == worker_id

    def test_get_latest_log_not_found(self) -> None:
        """测试获取不存在的最新日志"""
        latest = self.adapter.get_latest_log("wrk_not_exist")
        assert latest is None