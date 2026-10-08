"""
Tests for Worker Runtime State Service

Stage 1 Service Tests
"""

import pytest

from src.application.services.worker_runtime_state_service import WorkerRuntimeStateService
from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore
from src.infra.adapters.in_memory_worker_audit_log_store import InMemoryWorkerAuditLogStore
from src.infra.adapters.in_memory_worker_index_sync_adapter import InMemoryWorkerIndexSyncAdapter
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
from src.domain.exceptions import WorkerNotFoundException


class TestWorkerRuntimeStateService:
    """WorkerRuntimeStateService 测试"""

    @pytest.fixture
    def registry_store(self):
        """创建 registry store"""
        return InMemoryWorkerRegistryStore()

    @pytest.fixture
    def runtime_state_store(self):
        """创建 runtime state store"""
        return InMemoryWorkerRuntimeStateStore()

    @pytest.fixture
    def audit_log_store(self):
        """创建 audit log store"""
        return InMemoryWorkerAuditLogStore()

    @pytest.fixture
    def index_sync_adapter(self):
        """创建 index sync adapter"""
        return InMemoryWorkerIndexSyncAdapter()

    @pytest.fixture
    def service(self, registry_store, runtime_state_store, audit_log_store, index_sync_adapter):
        """创建服务实例"""
        return WorkerRuntimeStateService(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            audit_log_adapter=audit_log_store,
            index_sync_adapter=index_sync_adapter,
        )

    @pytest.fixture
    def sample_worker(self, registry_store):
        """创建示例 Worker"""
        worker = Worker(
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
        )
        registry_store.create(worker)
        return worker

    def test_set_online(self, service, sample_worker, runtime_state_store):
        """测试设置为在线"""
        # 初始化为 offline
        runtime_state_store.set_runtime_state(
            sample_worker.id,
            WorkerRuntimeState.OFFLINE
        )

        # 设置为 online
        updated = service.set_online(sample_worker.id, "test_user")

        assert updated.state.runtime_state == WorkerRuntimeState.ONLINE

    def test_set_online_disabled_worker(self, service, registry_store, runtime_state_store):
        """测试 disabled worker 不能设为 online"""
        # 创建 disabled worker
        worker = Worker(
            id="wrk_disabled_001",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Disabled Bot", handle="@disabled-bot"),
            responsibilities=["testing"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.TRUSTED,
            ),
            lifecycle_state=WorkerLifecycleState.DISABLED,
        )
        registry_store.create(worker)
        runtime_state_store.set_runtime_state(worker.id, WorkerRuntimeState.OFFLINE)

        # 尝试设置为 online
        with pytest.raises(ValueError, match="DISABLED"):
            service.set_online(worker.id)

    def test_set_online_inactive_worker(self, service, registry_store, runtime_state_store):
        """测试 inactive worker 不能设为 online"""
        # 创建 inactive worker
        worker = Worker(
            id="wrk_inactive_001",
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
        registry_store.create(worker)
        runtime_state_store.set_runtime_state(worker.id, WorkerRuntimeState.OFFLINE)

        # 尝试设置为 online
        with pytest.raises(ValueError, match="INACTIVE"):
            service.set_online(worker.id)

    def test_set_offline(self, service, sample_worker, runtime_state_store):
        """测试设置为离线"""
        # 初始化为 online
        runtime_state_store.set_runtime_state(
            sample_worker.id,
            WorkerRuntimeState.ONLINE
        )

        # 设置为 offline
        updated = service.set_offline(sample_worker.id, "test_user")

        assert updated.state.runtime_state == WorkerRuntimeState.OFFLINE

    def test_set_offline_disabled_worker(self, service, registry_store, runtime_state_store):
        """测试 disabled worker 可以设为 offline"""
        # 创建 disabled worker
        worker = Worker(
            id="wrk_disabled_002",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Disabled Bot", handle="@disabled-bot"),
            responsibilities=["testing"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.TRUSTED,
            ),
            lifecycle_state=WorkerLifecycleState.DISABLED,
        )
        registry_store.create(worker)
        runtime_state_store.set_runtime_state(worker.id, WorkerRuntimeState.ONLINE)

        # 设置为 offline（应该成功）
        updated = service.set_offline(worker.id)
        assert updated.state.runtime_state == WorkerRuntimeState.OFFLINE

    def test_set_online_creates_audit_log(self, service, sample_worker, runtime_state_store, audit_log_store):
        """测试设置 online 创建审计日志"""
        runtime_state_store.set_runtime_state(sample_worker.id, WorkerRuntimeState.OFFLINE)

        service.set_online(sample_worker.id, "test_user")

        logs = audit_log_store.list_logs(worker_id=sample_worker.id)
        assert len(logs) == 1
        assert logs[0].action.value == "runtime_state_changed"

    def test_set_online_triggers_index_sync(self, service, sample_worker, runtime_state_store, index_sync_adapter):
        """测试设置 online 触发索引同步"""
        runtime_state_store.set_runtime_state(sample_worker.id, WorkerRuntimeState.OFFLINE)

        service.set_online(sample_worker.id, "test_user")

        assert index_sync_adapter.has_event("runtime_state_changed")

    def test_get_runtime_state(self, service, sample_worker, runtime_state_store):
        """测试获取运行态"""
        runtime_state_store.set_runtime_state(sample_worker.id, WorkerRuntimeState.ONLINE)

        state = service.get_runtime_state(sample_worker.id)

        assert state == WorkerRuntimeState.ONLINE

    def test_get_runtime_state_not_found(self, service):
        """测试获取不存在 worker 的运行态"""
        state = service.get_runtime_state("wrk_not_exist")
        assert state is None

    def test_worker_not_found(self, service):
        """测试 worker 不存在"""
        with pytest.raises(WorkerNotFoundException):
            service.set_online("wrk_not_exist")

        with pytest.raises(WorkerNotFoundException):
            service.set_offline("wrk_not_exist")

    def test_idempotent_set_online(self, service, sample_worker, runtime_state_store):
        """测试重复设置 online 是幂等的"""
        runtime_state_store.set_runtime_state(sample_worker.id, WorkerRuntimeState.ONLINE)

        # 再次设置 online（应该无变化）
        updated = service.set_online(sample_worker.id)
        assert updated.state.runtime_state == WorkerRuntimeState.ONLINE

    def test_idempotent_set_offline(self, service, sample_worker, runtime_state_store):
        """测试重复设置 offline 是幂等的"""
        runtime_state_store.set_runtime_state(sample_worker.id, WorkerRuntimeState.OFFLINE)

        # 再次设置 offline（应该无变化）
        updated = service.set_offline(sample_worker.id)
        assert updated.state.runtime_state == WorkerRuntimeState.OFFLINE

    def test_set_online_then_offline_no_version_conflict(self, service, sample_worker, runtime_state_store):
        """
        测试 set_online 后再 set_offline 不应触发 version conflict

        这是 fix for version conflict bug 的回归测试：
        - set_online 会更新 worker 的 version
        - set_offline 使用 fresh read 获取最新 version
        - 因此不应发生 version conflict
        """
        # 初始化为 offline
        runtime_state_store.set_runtime_state(sample_worker.id, WorkerRuntimeState.OFFLINE)

        # 先设置为 online
        updated_online = service.set_online(sample_worker.id, "test_user")
        assert updated_online.state.runtime_state == WorkerRuntimeState.ONLINE
        online_version = updated_online.version
        assert online_version > sample_worker.version

        # 再设置为 offline - 应使用 fresh read 获取最新 version
        updated_offline = service.set_offline(sample_worker.id, "test_user")
        assert updated_offline.state.runtime_state == WorkerRuntimeState.OFFLINE
        offline_version = updated_offline.version
        assert offline_version > online_version

    def test_online_offline_online_chain_no_version_conflict(self, service, sample_worker, runtime_state_store):
        """
        测试连续切换 online -> offline -> online 不应触发 version conflict

        模拟生产环境中多次状态切换的场景。
        每次切换都使用 fresh read 获取最新 version。
        """
        # 初始化为 offline
        runtime_state_store.set_runtime_state(sample_worker.id, WorkerRuntimeState.OFFLINE)

        version = sample_worker.version

        # 第一轮: offline -> online
        worker = service.set_online(sample_worker.id, "test_user")
        assert worker.state.runtime_state == WorkerRuntimeState.ONLINE
        assert worker.version > version
        version = worker.version

        # 第一轮: online -> offline
        worker = service.set_offline(sample_worker.id, "test_user")
        assert worker.state.runtime_state == WorkerRuntimeState.OFFLINE
        assert worker.version > version
        version = worker.version

        # 第二轮: offline -> online
        worker = service.set_online(sample_worker.id, "test_user")
        assert worker.state.runtime_state == WorkerRuntimeState.ONLINE
        assert worker.version > version
        version = worker.version

        # 第二轮: online -> offline
        worker = service.set_offline(sample_worker.id, "test_user")
        assert worker.state.runtime_state == WorkerRuntimeState.OFFLINE
        assert worker.version > version