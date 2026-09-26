"""
Unit Tests for RegistryWorkerProfileSource

测试从 Worker Registry 构建 WorkerProfile 的功能。
"""

import pytest

from src.infra.worker_profiles.sources.registry_worker_profile_source import RegistryWorkerProfileSource
from src.domain.models.worker import (
    Worker,
    WorkerType,
    WorkerIdentity,
    Capability,
    CapabilityLevel,
    WorkerState,
    Availability,
    TrustLevel,
)
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState


def create_test_worker(
    worker_id: str,
    name: str,
    description: str = "",
    responsibilities: list[str] = None,
    domains: list[str] = None,
    capabilities: list[str] = None,
    lifecycle_state: WorkerLifecycleState = WorkerLifecycleState.ACTIVE,
    runtime_state: WorkerRuntimeState = WorkerRuntimeState.ONLINE,
) -> Worker:
    """Helper function to create a test Worker"""
    return Worker(
        id=worker_id,
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name=name,
            handle=f"@{worker_id}",
            description=description if description else None,
        ),
        responsibilities=responsibilities or ["general"],
        domains=domains or [],
        capabilities=[
            Capability(name=cap, level=CapabilityLevel.EXPERT)
            for cap in (capabilities or ["general"])
        ],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.GUARDED,
            runtime_state=runtime_state,
        ),
        lifecycle_state=lifecycle_state,
    )


class MockRegistryStore:
    """Mock Registry Store for testing"""

    def __init__(self, workers: list[Worker]):
        self._workers = {w.id: w for w in workers}

    def list(
        self,
        lifecycle_states=None,
        source_types=None,
        domains=None,
        limit=None,
        offset=None,
    ) -> list[Worker]:
        """Mock list method - matches WorkerRegistryStoreAdapter protocol"""
        result = list(self._workers.values())
        if lifecycle_states:
            result = [w for w in result if w.lifecycle_state in lifecycle_states]
        return result

    def get_by_id(self, worker_id: str):
        """Mock get_by_id method - matches WorkerRegistryStoreAdapter protocol"""
        return self._workers.get(worker_id)


class MockRuntimeStateStore:
    """Mock Runtime State Store for testing"""

    def __init__(self, states: dict):
        self._states = states

    def get_runtime_state(self, worker_id: str):
        """Return WorkerRuntimeState directly (matches WorkerRuntimeStateStoreAdapter protocol)"""
        return self._states.get(worker_id)


class TestRegistryWorkerProfileSource:
    """Tests for RegistryWorkerProfileSource"""

    def test_scan_empty_registry(self):
        """测试空 Registry 返回空结果"""
        store = MockRegistryStore([])
        source = RegistryWorkerProfileSource(store)

        result = source.scan()

        assert len(result.profiles) == 0
        assert len(result.scan_warnings) == 0

    def test_scan_single_active_worker(self):
        """测试单个活跃 Worker"""
        worker = create_test_worker(
            worker_id="wrk_test_001",
            name="测试专家",
            description="这是一个测试专家",
            responsibilities=["架构设计", "代码审查"],
            domains=["技术架构"],
            capabilities=["微服务"],
        )

        store = MockRegistryStore([worker])
        source = RegistryWorkerProfileSource(store)

        result = source.scan()

        assert len(result.profiles) == 1
        assert result.profiles[0].staff_id == "wrk_test_001"
        assert result.profiles[0].profile_id == "default"
        assert "测试专家" in result.profiles[0].searchable_text

    def test_scan_filters_inactive_workers(self):
        """测试过滤非活跃 Worker"""
        workers = [
            create_test_worker(
                worker_id="wrk_active",
                name="活跃专家",
                lifecycle_state=WorkerLifecycleState.ACTIVE,
            ),
            create_test_worker(
                worker_id="wrk_inactive",
                name="非活跃专家",
                lifecycle_state=WorkerLifecycleState.DISABLED,
            ),
        ]

        store = MockRegistryStore(workers)
        source = RegistryWorkerProfileSource(store)

        result = source.scan()

        assert len(result.profiles) == 1
        assert result.profiles[0].staff_id == "wrk_active"

    def test_scan_filters_offline_workers(self):
        """测试过滤离线 Worker（使用 runtime state）"""
        workers = [
            create_test_worker(
                worker_id="wrk_online",
                name="在线专家",
                lifecycle_state=WorkerLifecycleState.ACTIVE,
                runtime_state=WorkerRuntimeState.ONLINE,
            ),
            create_test_worker(
                worker_id="wrk_offline",
                name="离线专家",
                lifecycle_state=WorkerLifecycleState.ACTIVE,
                runtime_state=WorkerRuntimeState.OFFLINE,
            ),
        ]

        runtime_states = {
            "wrk_online": WorkerRuntimeState.ONLINE,
            "wrk_offline": WorkerRuntimeState.OFFLINE,
        }

        store = MockRegistryStore(workers)
        runtime_store = MockRuntimeStateStore(runtime_states)

        source = RegistryWorkerProfileSource(
            registry_store=store,
            runtime_state_store=runtime_store,
            include_offline=False,
        )

        result = source.scan()

        assert len(result.profiles) == 1
        assert result.profiles[0].staff_id == "wrk_online"

    def test_get_profile_existing(self):
        """测试获取存在的 Profile"""
        worker = create_test_worker(
            worker_id="wrk_test_002",
            name="测试专家",
            description="测试描述",
        )

        store = MockRegistryStore([worker])
        source = RegistryWorkerProfileSource(store)

        profile = source.get_profile("wrk_test_002", "default")

        assert profile is not None
        assert profile.staff_id == "wrk_test_002"

    def test_get_profile_nonexistent(self):
        """测试获取不存在的 Profile"""
        store = MockRegistryStore([])
        source = RegistryWorkerProfileSource(store)

        profile = source.get_profile("wrk_nonexistent", "default")

        assert profile is None

    def test_profile_contains_capabilities(self):
        """测试 Profile 包含 capabilities 信息"""
        worker = create_test_worker(
            worker_id="wrk_test_003",
            name="技术专家",
            description="技术专家描述",
            responsibilities=["架构设计", "技术决策"],
            domains=["技术架构"],
            capabilities=["微服务架构", "分布式系统"],
        )

        store = MockRegistryStore([worker])
        source = RegistryWorkerProfileSource(store)

        result = source.scan()

        assert len(result.profiles) == 1
        profile = result.profiles[0]

        # 检查 searchable_text 包含能力信息
        searchable = profile.searchable_text.lower()
        assert "技术架构" in searchable or "架构" in searchable
        assert "微服务架构" in searchable or "微服务" in searchable

    def test_profile_type_from_worker_type(self):
        """测试 Profile 类型来自 Worker 类型"""
        bot_worker = create_test_worker(
            worker_id="wrk_bot",
            name="Bot专家",
        )
        # Modify type after creation for testing
        bot_worker = Worker(
            id="wrk_bot",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Bot专家", handle="@wrk_bot"),
            responsibilities=["general"],
            capabilities=[Capability(name="general", level=CapabilityLevel.EXPERT)],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.GUARDED,
                runtime_state=WorkerRuntimeState.ONLINE,
            ),
            lifecycle_state=WorkerLifecycleState.ACTIVE,
        )
        human_worker = Worker(
            id="wrk_human",
            type=WorkerType.HUMAN,
            identity=WorkerIdentity(name="人类专家", handle="@wrk_human"),
            responsibilities=["general"],
            capabilities=[Capability(name="general", level=CapabilityLevel.EXPERT)],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.GUARDED,
                runtime_state=WorkerRuntimeState.ONLINE,
            ),
            lifecycle_state=WorkerLifecycleState.ACTIVE,
        )

        store = MockRegistryStore([bot_worker, human_worker])
        source = RegistryWorkerProfileSource(store)

        result = source.scan()

        profiles = {p.staff_id: p for p in result.profiles}
        assert profiles["wrk_bot"].profile_type.value == "bot"
        assert profiles["wrk_human"].profile_type.value == "default"  # HUMAN -> DEFAULT


__all__ = ["TestRegistryWorkerProfileSource"]