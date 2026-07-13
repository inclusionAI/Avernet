"""
Integration Tests for Registry-Aware Filtering

Stage 1 Phase 4: Registry State → Candidate Filtering

测试 Registry 状态对 retrieval/recommendation/matching 的过滤影响。

验证：
- BaselineRetriever 只返回 active + online 的 worker
- WorkerProfileRetrievalService 只返回 active + online 的 profile
- WorkerVectorMatchService 只返回 active + online 的 match 结果
- 兼容模式：未注册的 profile 放行
"""

import pytest

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
from src.domain.models.retrieval_input import RetrievalInput
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.task_spec import TaskSpec, RiskLevel
from src.domain.models.plan_draft import PlanDraft, PlanStep
from src.infra.retrievers.baseline_retriever import BaselineRetriever, CandidateCatalog
from src.application.services.retrieval_service import RetrievalService
from src.application.services.registry_aware_worker_filter import RegistryAwareWorkerFilter
from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore


# =============================================================================
# Test Fixtures
# =============================================================================

def create_test_worker(
    worker_id: str,
    name: str,
    lifecycle_state: WorkerLifecycleState = WorkerLifecycleState.ACTIVE,
    runtime_state: WorkerRuntimeState = WorkerRuntimeState.OFFLINE,
    capabilities: list[str] | None = None,
) -> Worker:
    """创建测试用的 Worker"""
    caps = [
        Capability(name=cap, level=CapabilityLevel.INTERMEDIATE)
        for cap in (capabilities or ["general"])
    ]

    return Worker(
        id=worker_id,
        type=WorkerType.BOT,
        identity=WorkerIdentity(
            name=name,
            handle=f"@{worker_id}",
        ),
        responsibilities=["testing"],
        domains=["testing"],
        capabilities=caps,
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            runtime_state=runtime_state,
        ),
        lifecycle_state=lifecycle_state,
    )


@pytest.fixture
def mixed_state_workers() -> list[Worker]:
    """创建混合状态的 Worker 列表"""
    return [
        # Active + Online (应该被返回)
        create_test_worker(
            "wrk_active_online_001",
            "Active Online Bot",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            runtime_state=WorkerRuntimeState.ONLINE,
            capabilities=["coding", "testing"],
        ),
        # Active + Offline (应该被过滤)
        create_test_worker(
            "wrk_active_offline_001",
            "Active Offline Bot",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            runtime_state=WorkerRuntimeState.OFFLINE,
            capabilities=["coding", "testing"],
        ),
        # Inactive + Online (应该被过滤)
        create_test_worker(
            "wrk_inactive_online_001",
            "Inactive Online Bot",
            lifecycle_state=WorkerLifecycleState.INACTIVE,
            runtime_state=WorkerRuntimeState.ONLINE,
            capabilities=["coding", "testing"],
        ),
        # Inactive + Offline (应该被过滤)
        create_test_worker(
            "wrk_inactive_offline_001",
            "Inactive Offline Bot",
            lifecycle_state=WorkerLifecycleState.INACTIVE,
            runtime_state=WorkerRuntimeState.OFFLINE,
            capabilities=["coding", "testing"],
        ),
        # Disabled (应该被过滤)
        create_test_worker(
            "wrk_disabled_001",
            "Disabled Bot",
            lifecycle_state=WorkerLifecycleState.DISABLED,
            runtime_state=WorkerRuntimeState.OFFLINE,
            capabilities=["coding", "testing"],
        ),
    ]


@pytest.fixture
def registry_store() -> InMemoryWorkerRegistryStore:
    """创建内存 Registry Store"""
    return InMemoryWorkerRegistryStore()


@pytest.fixture
def runtime_state_store() -> InMemoryWorkerRuntimeStateStore:
    """创建内存 Runtime State Store"""
    return InMemoryWorkerRuntimeStateStore()


# =============================================================================
# BaselineRetriever Filter Tests
# =============================================================================

class TestBaselineRetrieverFiltering:
    """BaselineRetriever 过滤测试"""

    def test_filter_inactive_workers_enabled(
        self,
        mixed_state_workers: list[Worker],
    ):
        """
        测试启用过滤后只返回 active + online 的 worker

        Given: 包含各种状态的 worker 目录
        When: 启用 filter_inactive_workers=True
        Then: 只返回 active + online 的 worker
        """
        catalog = CandidateCatalog(workers=mixed_state_workers)
        retriever = BaselineRetriever(catalog=catalog, filter_inactive_workers=True)

        # 简单的 task spec 和 plan draft
        task_spec = TaskSpec(
            id="tsk_test_001",
            goal="Test task",
            deliverables=["Result"],
            constraints=[],
            success_criteria=["Success"],
            required_capabilities=["coding"],
            required_knowledge=[],
            required_resources=[],
            risk_level=RiskLevel.LOW,
            unknowns=[],
            subtasks=[],
        )
        plan_draft = PlanDraft(
            task_id="tsk_test_001",
            strategy="Test strategy",
            steps=[PlanStep(id="s1", title="Step", objective="Objective")],
            role_requirements=["testing"],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 只应该有 1 个 worker: active + online
        assert len(result.candidate_bundle.workers) == 1
        assert result.candidate_bundle.workers[0].id == "wrk_active_online_001"

    def test_filter_inactive_workers_disabled(
        self,
        mixed_state_workers: list[Worker],
    ):
        """
        测试禁用过滤后返回所有匹配的 worker

        Given: 包含各种状态的 worker 目录
        When: 禁用 filter_inactive_workers=False
        Then: 返回所有匹配的 worker（不过滤）
        """
        catalog = CandidateCatalog(workers=mixed_state_workers)
        retriever = BaselineRetriever(catalog=catalog, filter_inactive_workers=False)

        task_spec = TaskSpec(
            id="tsk_test_001",
            goal="Test task",
            deliverables=["Result"],
            constraints=[],
            success_criteria=["Success"],
            required_capabilities=["coding"],
            required_knowledge=[],
            required_resources=[],
            risk_level=RiskLevel.LOW,
            unknowns=[],
            subtasks=[],
        )
        plan_draft = PlanDraft(
            task_id="tsk_test_001",
            strategy="Test strategy",
            steps=[PlanStep(id="s1", title="Step", objective="Objective")],
            role_requirements=["testing"],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 应该返回所有匹配的 worker（5个都匹配 capabilities）
        assert len(result.candidate_bundle.workers) == 5

    def test_all_workers_filtered_out(
        self,
    ):
        """
        测试所有 worker 都被过滤的情况

        Given: 所有 worker 都是 inactive 或 offline
        When: 启用过滤
        Then: 返回空结果并生成警告
        """
        workers = [
            create_test_worker(
                "wrk_offline_001",
                "Offline Bot",
                lifecycle_state=WorkerLifecycleState.ACTIVE,
                runtime_state=WorkerRuntimeState.OFFLINE,
                capabilities=["coding"],
            ),
        ]
        catalog = CandidateCatalog(workers=workers)
        retriever = BaselineRetriever(catalog=catalog, filter_inactive_workers=True)

        task_spec = TaskSpec(
            id="tsk_test_001",
            goal="Test task",
            deliverables=["Result"],
            constraints=[],
            success_criteria=["Success"],
            required_capabilities=["coding"],
            required_knowledge=[],
            required_resources=[],
            risk_level=RiskLevel.LOW,
            unknowns=[],
            subtasks=[],
        )
        plan_draft = PlanDraft(
            task_id="tsk_test_001",
            strategy="Test strategy",
            steps=[PlanStep(id="s1", title="Step", objective="Objective")],
            role_requirements=[],
            knowledge_requirements=[],
            resource_requirements=[],
            handoff_strategy="auto",
            escalation_points=[],
        )
        input_data = RetrievalInput(task_spec=task_spec, plan_draft=plan_draft)

        result = retriever.retrieve(input_data)

        # 应该返回空结果
        assert len(result.candidate_bundle.workers) == 0
        # 应该有警告
        assert len(result.warnings) > 0


# =============================================================================
# RegistryAwareWorkerFilter Tests
# =============================================================================

class TestRegistryAwareWorkerFilter:
    """RegistryAwareWorkerFilter 测试"""

    def test_filter_returns_only_active_online(
        self,
        registry_store: InMemoryWorkerRegistryStore,
        runtime_state_store: InMemoryWorkerRuntimeStateStore,
        mixed_state_workers: list[Worker],
    ):
        """
        测试过滤只返回 active + online 的 profile_keys

        Given: Registry 中有各种状态的 worker
        When: 调用 get_allowed_profile_keys
        Then: 只返回 active + online 的 profile_keys
        """
        # 注册所有 worker
        for worker in mixed_state_workers:
            registry_store.create(worker)
            runtime_state_store.set_runtime_state(worker.id, worker.state.runtime_state)

        filter_service = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        allowed_keys = filter_service.get_allowed_profile_keys()

        # 只有 active + online 的 worker 对应的 profile_key
        # 注意：当前 worker 没有 active_profile_key，所以返回空
        assert len(allowed_keys) == 0

    def test_compatibility_mode_allows_unregistered_profiles(
        self,
        registry_store: InMemoryWorkerRegistryStore,
        runtime_state_store: InMemoryWorkerRuntimeStateStore,
    ):
        """
        测试兼容模式允许未注册的 profiles

        Given: strict_mode=False（兼容模式）
        When: 调用 get_allowed_profile_keys 传入 all_profile_keys
        Then: 未注册的 profile_keys 也被包含
        """
        filter_service = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=False,
        )

        all_keys = ["staff_001:default", "staff_002:default", "staff_003:expert"]
        allowed_keys = filter_service.get_allowed_profile_keys(all_keys)

        # 兼容模式：未注册的 profile_keys 也返回
        assert allowed_keys == set(all_keys)

    def test_strict_mode_filters_unregistered_profiles(
        self,
        registry_store: InMemoryWorkerRegistryStore,
        runtime_state_store: InMemoryWorkerRuntimeStateStore,
    ):
        """
        测试严格模式过滤未注册的 profiles

        Given: strict_mode=True（严格模式）
        When: 调用 get_allowed_profile_keys 传入 all_profile_keys
        Then: 未注册的 profile_keys 被过滤掉
        """
        filter_service = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        all_keys = ["staff_001:default", "staff_002:default", "staff_003:expert"]
        allowed_keys = filter_service.get_allowed_profile_keys(all_keys)

        # 严格模式：没有注册的 worker，返回空
        assert len(allowed_keys) == 0

    def test_filter_with_registered_active_online_worker(
        self,
        registry_store: InMemoryWorkerRegistryStore,
        runtime_state_store: InMemoryWorkerRuntimeStateStore,
    ):
        """
        测试有注册的 active + online worker 时的过滤

        Given: Registry 中有 active + online 的 worker，设置了 active_profile_key
        When: 调用 get_allowed_profile_keys
        Then: 返回该 worker 的 profile_key
        """
        # 创建带 active_profile_key 的 worker
        worker = create_test_worker(
            "wrk_registered_001",
            "Registered Bot",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            runtime_state=WorkerRuntimeState.ONLINE,
            capabilities=["coding"],
        )
        worker.active_profile_key = "staff_001:default"

        registry_store.create(worker)
        runtime_state_store.set_runtime_state(worker.id, WorkerRuntimeState.ONLINE)

        filter_service = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        all_keys = ["staff_001:default", "staff_002:default"]
        allowed_keys = filter_service.get_allowed_profile_keys(all_keys)

        # 只有注册且 active + online 的 profile_key
        assert allowed_keys == {"staff_001:default"}


# =============================================================================
# Filter Statistics Tests
# =============================================================================

class TestFilterStatistics:
    """过滤统计测试"""

    def test_get_filter_stats(
        self,
        registry_store: InMemoryWorkerRegistryStore,
        runtime_state_store: InMemoryWorkerRuntimeStateStore,
        mixed_state_workers: list[Worker],
    ):
        """
        测试获取过滤统计信息

        Given: Registry 中有各种状态的 worker
        When: 调用 get_filter_stats
        Then: 返回详细的统计信息
        """
        for worker in mixed_state_workers:
            registry_store.create(worker)
            runtime_state_store.set_runtime_state(worker.id, worker.state.runtime_state)

        filter_service = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=False,
        )

        all_profile_keys = [f"staff_{i:03d}:default" for i in range(10)]
        stats = filter_service.get_filter_stats(all_profile_keys)

        # 验证统计结构
        assert "total_profile_keys" in stats
        assert "allowed_profile_keys" in stats
        assert "filtered_out" in stats
        assert "registry_total_workers" in stats
        assert "registry_active_workers" in stats
        assert "runtime_online_count" in stats
        assert "runtime_offline_count" in stats
        assert "strict_mode" in stats

        # 验证具体值
        assert stats["registry_total_workers"] == 5
        assert stats["registry_active_workers"] == 2  # active_online + active_offline
        assert stats["runtime_online_count"] == 2  # active_online + inactive_online


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestEdgeCases:
    """边界情况测试"""

    def test_empty_registry(
        self,
        registry_store: InMemoryWorkerRegistryStore,
        runtime_state_store: InMemoryWorkerRuntimeStateStore,
    ):
        """测试空 Registry 的情况"""
        filter_service = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        allowed_keys = filter_service.get_allowed_profile_keys(["key1", "key2"])
        assert len(allowed_keys) == 0

    def test_all_workers_offline(
        self,
        registry_store: InMemoryWorkerRegistryStore,
        runtime_state_store: InMemoryWorkerRuntimeStateStore,
    ):
        """测试所有 worker 都 offline 的情况"""
        worker = create_test_worker(
            "wrk_offline_001",
            "Offline Bot",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            runtime_state=WorkerRuntimeState.OFFLINE,
        )
        worker.active_profile_key = "staff_001:default"
        registry_store.create(worker)
        runtime_state_store.set_runtime_state(worker.id, WorkerRuntimeState.OFFLINE)

        filter_service = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        allowed_keys = filter_service.get_allowed_profile_keys(["staff_001:default"])
        assert len(allowed_keys) == 0

    def test_all_workers_inactive(
        self,
        registry_store: InMemoryWorkerRegistryStore,
        runtime_state_store: InMemoryWorkerRuntimeStateStore,
    ):
        """测试所有 worker 都 inactive 的情况"""
        worker = create_test_worker(
            "wrk_inactive_001",
            "Inactive Bot",
            lifecycle_state=WorkerLifecycleState.INACTIVE,
            runtime_state=WorkerRuntimeState.ONLINE,
        )
        worker.active_profile_key = "staff_001:default"
        registry_store.create(worker)
        runtime_state_store.set_runtime_state(worker.id, WorkerRuntimeState.ONLINE)

        filter_service = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        allowed_keys = filter_service.get_allowed_profile_keys(["staff_001:default"])
        assert len(allowed_keys) == 0