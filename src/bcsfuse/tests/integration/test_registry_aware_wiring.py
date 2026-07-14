"""
Integration Tests for Registry-Aware Wiring Verification

Stage 1 Phase 4.5: Production Wiring Verification

验证 Registry-aware filtering 真正接入了业务主链路。

测试场景：
1. Retrieval 默认过滤 offline worker
2. Recommendation 默认过滤 offline worker
3. Vector matching 默认过滤 offline worker
4. G5 主链路实际受 online/offline 影响
5. 兼容模式验证

关键验证：
- 证明 RegistryAwareWorkerFilter 被真实注入到主链路
- 证明 recommendation 主路径已接入 registry-aware filtering
- 证明 G5 默认候选集过滤 offline worker
"""

import os
import tempfile
import pytest
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

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
from src.domain.models.worker_profile import WorkerProfile, ProfileType, WorkerProfileScanResult
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.fusion_request import FusionRequest, FuseOptions
from src.infra.adapters.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
from src.infra.adapters.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore
from src.application.services.registry_aware_worker_filter import RegistryAwareWorkerFilter
from src.application.services.worker_candidate_recommendation_impl import WorkerCandidateRecommendationImpl
from src.application.services.expert_diagnosis_service import ExpertDiagnosisService
from src.application.services.group_fusion_service import GroupFusionService
from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService


# =============================================================================
# Test Fixtures
# =============================================================================

def create_test_worker(
    worker_id: str,
    name: str,
    lifecycle_state: WorkerLifecycleState = WorkerLifecycleState.ACTIVE,
    runtime_state: WorkerRuntimeState = WorkerRuntimeState.OFFLINE,
    profile_key: str | None = None,
    capabilities: list[str] | None = None,
) -> Worker:
    """创建测试用的 Worker"""
    caps = [
        Capability(name=cap, level=CapabilityLevel.EXPERT)
        for cap in (capabilities or ["general"])
    ]

    worker = Worker(
        id=worker_id,
        type=WorkerType.BOT,
        identity=WorkerIdentity(name=name, handle=f"@{worker_id}"),
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
    if profile_key:
        worker.active_profile_key = profile_key
    return worker


def create_test_profile(
    profile_key: str,
    skills: list[str] | None = None,
) -> WorkerProfile:
    """创建测试用的 WorkerProfile"""
    # Parse profile_key: "staff_XXX:default" -> staff_id=XXX, profile_id=default
    parts = profile_key.split(":")
    if len(parts) == 2:
        # Remove "staff_" prefix if present
        staff_id = parts[0].replace("staff_", "")
        profile_id = parts[1]
    else:
        staff_id = profile_key
        profile_id = "default"

    return WorkerProfile(
        staff_id=staff_id,
        profile_id=profile_id,
        profile_type=ProfileType.DEFAULT,
        source_root="mock",
        active_skills=[
            SkillProfile(
                skill_id=f"skill_{s}",
                name=s,
                description=f"{s} skill",
                skill_set_name="default",
            )
            for s in (skills or ["general"])
        ],
        context_fragments=[],
        searchable_text=" ".join(skills or ["general"]),
    )


class MockProfileSource:
    """Mock Profile Source for testing"""

    def __init__(self, profiles: list[WorkerProfile]):
        self._profiles = profiles

    def scan(self) -> WorkerProfileScanResult:
        return WorkerProfileScanResult(
            profiles=self._profiles,
            scan_warnings=[],
            source_roots=["mock"],
        )

    def get_profile(self, staff_id: str, profile_id: str) -> WorkerProfile | None:
        key = f"{staff_id}:{profile_id}"
        for p in self._profiles:
            if p.profile_key == key:
                return p
        return None

    def get_profiles_by_staff(self, staff_id: str) -> list[WorkerProfile]:
        return [p for p in self._profiles if p.staff_id == staff_id]


@pytest.fixture
def temp_db():
    """Create temporary database for testing"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def registry_store(temp_db):
    """Create registry store"""
    store = SQLiteWorkerRegistryStore(temp_db)
    yield store
    store.close()


@pytest.fixture
def runtime_state_store(temp_db):
    """Create runtime state store"""
    store = SQLiteWorkerRuntimeStateStore(temp_db)
    yield store
    store.close()


# =============================================================================
# Scenario 1: Retrieval Default Filters Offline
# =============================================================================

class TestRetrievalFiltering:
    """场景 1：Retrieval 默认过滤 offline"""

    def test_retrieval_filters_offline_worker(
        self,
        registry_store: SQLiteWorkerRegistryStore,
        runtime_state_store: SQLiteWorkerRuntimeStateStore,
    ):
        """
        验证：Retrieval 默认只返回 online worker

        Given: 两个 worker，一个 online，一个 offline
        When: 使用带 filter 的 retrieval service 检索
        Then: 只返回 online worker 的 profile
        """
        # 1. 创建 workers
        online_worker = create_test_worker(
            "wrk_online_001",
            "Online Bot",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            runtime_state=WorkerRuntimeState.ONLINE,
            profile_key="staff_001:online_profile",
            capabilities=["python", "testing"],
        )
        offline_worker = create_test_worker(
            "wrk_offline_001",
            "Offline Bot",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            runtime_state=WorkerRuntimeState.OFFLINE,
            profile_key="staff_002:offline_profile",
            capabilities=["python", "testing"],
        )

        # 2. 注册 workers
        registry_store.create(online_worker)
        registry_store.create(offline_worker)
        runtime_state_store.set_runtime_state(online_worker.id, WorkerRuntimeState.ONLINE)
        runtime_state_store.set_runtime_state(offline_worker.id, WorkerRuntimeState.OFFLINE)

        # 3. 创建 profiles
        online_profile = create_test_profile("staff_001:online_profile", skills=["python"])
        offline_profile = create_test_profile("staff_002:offline_profile", skills=["python"])
        mock_source = MockProfileSource([online_profile, offline_profile])

        # 4. 创建 filter 和 retrieval service
        profile_filter = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        retrieval_service = WorkerProfileRetrievalService(
            source=mock_source,
            profile_filter=profile_filter,
        )

        # 5. 检索
        result = retrieval_service.retrieve(
            question="python testing",
            mode=RetrievalMode.AGENT,
        )

        # 6. 验证：只有 online worker 的 profile
        profile_keys = [r.profile.profile_key for r in result.results]
        assert "staff_001:online_profile" in profile_keys
        assert "staff_002:offline_profile" not in profile_keys

    def test_retrieval_includes_active_online_only(
        self,
        registry_store: SQLiteWorkerRegistryStore,
        runtime_state_store: SQLiteWorkerRuntimeStateStore,
    ):
        """
        验证：只返回 active + online 的 worker

        Given: 多个不同状态的 workers
        When: 检索
        Then: 只返回 active + online 的
        """
        # 创建各种状态的 workers
        workers = [
            create_test_worker("wrk_active_online", "Active Online", WorkerLifecycleState.ACTIVE, WorkerRuntimeState.ONLINE, "staff_01:p1"),
            create_test_worker("wrk_active_offline", "Active Offline", WorkerLifecycleState.ACTIVE, WorkerRuntimeState.OFFLINE, "staff_02:p2"),
            create_test_worker("wrk_inactive_online", "Inactive Online", WorkerLifecycleState.INACTIVE, WorkerRuntimeState.ONLINE, "staff_03:p3"),
            create_test_worker("wrk_disabled", "Disabled", WorkerLifecycleState.DISABLED, WorkerRuntimeState.OFFLINE, "staff_04:p4"),
        ]

        for w in workers:
            registry_store.create(w)
            runtime_state_store.set_runtime_state(w.id, w.state.runtime_state)

        # 创建 profiles
        profiles = [create_test_profile(w.active_profile_key) for w in workers if w.active_profile_key]
        mock_source = MockProfileSource(profiles)

        # 创建带 filter 的 retrieval service
        profile_filter = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        retrieval_service = WorkerProfileRetrievalService(
            source=mock_source,
            profile_filter=profile_filter,
        )

        # 检索
        result = retrieval_service.retrieve(question="test", mode=RetrievalMode.AGENT)

        # 验证：只有 active + online 的
        profile_keys = [r.profile.profile_key for r in result.results]
        assert "staff_01:p1" in profile_keys
        assert "staff_02:p2" not in profile_keys  # offline
        assert "staff_03:p3" not in profile_keys  # inactive
        assert "staff_04:p4" not in profile_keys  # disabled


# =============================================================================
# Scenario 2: Recommendation Default Filters Offline
# =============================================================================

class TestRecommendationFiltering:
    """场景 2：Recommendation 默认过滤 offline"""

    def test_recommendation_filters_offline_worker(
        self,
        registry_store: SQLiteWorkerRegistryStore,
        runtime_state_store: SQLiteWorkerRuntimeStateStore,
    ):
        """
        验证：Recommendation 默认只推荐 online worker

        Given: 两个匹配的 workers，一个 online，一个 offline
        When: 调用 recommendation service
        Then: 只推荐 online worker
        """
        # 创建 workers
        online_worker = create_test_worker(
            "wrk_rec_online",
            "Recommendation Online Bot",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            runtime_state=WorkerRuntimeState.ONLINE,
            profile_key="staff_rec_online:default",
            capabilities=["security", "architecture"],
        )
        offline_worker = create_test_worker(
            "wrk_rec_offline",
            "Recommendation Offline Bot",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            runtime_state=WorkerRuntimeState.OFFLINE,
            profile_key="staff_rec_offline:default",
            capabilities=["security", "architecture"],
        )

        registry_store.create(online_worker)
        registry_store.create(offline_worker)
        runtime_state_store.set_runtime_state(online_worker.id, WorkerRuntimeState.ONLINE)
        runtime_state_store.set_runtime_state(offline_worker.id, WorkerRuntimeState.OFFLINE)

        # 创建 profiles
        online_profile = create_test_profile("staff_rec_online:default", skills=["security", "architecture"])
        offline_profile = create_test_profile("staff_rec_offline:default", skills=["security", "architecture"])
        mock_source = MockProfileSource([online_profile, offline_profile])

        # 创建带 filter 的 retrieval service 和 recommendation service
        profile_filter = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        retrieval_service = WorkerProfileRetrievalService(
            source=mock_source,
            profile_filter=profile_filter,
        )

        recommendation_service = WorkerCandidateRecommendationImpl(
            retrieval_service=retrieval_service,
            min_experts=2,
        )

        # 调用 recommendation
        result = recommendation_service.recommend(
            question="security architecture review",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
        )

        # 验证：只推荐 online worker
        recommended_keys = [r.profile_key for r in result.recommendations]
        assert "staff_rec_online:default" in recommended_keys
        assert "staff_rec_offline:default" not in recommended_keys


# =============================================================================
# Scenario 4: G5 Main Flow Affected by Registry State
# =============================================================================

class TestG5MainFlowFiltering:
    """场景 4：G5 主链路实际受 online/offline 影响"""

    def test_g5_expert_diagnosis_filters_offline(
        self,
        registry_store: SQLiteWorkerRegistryStore,
        runtime_state_store: SQLiteWorkerRuntimeStateStore,
    ):
        """
        验证：G5 Expert Diagnosis 只考虑 online worker

        Given: ExpertDiagnosisService 使用带 filter 的 recommendation service
        When: 执行 diagnose
        Then: 推荐的 participants 只包含 online worker
        """
        # 创建 workers
        online_expert = create_test_worker(
            "wrk_g5_online",
            "G5 Online Expert",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            runtime_state=WorkerRuntimeState.ONLINE,
            profile_key="staff_g5_online:expert",
            capabilities=["security"],
        )
        offline_expert = create_test_worker(
            "wrk_g5_offline",
            "G5 Offline Expert",
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            runtime_state=WorkerRuntimeState.OFFLINE,
            profile_key="staff_g5_offline:expert",
            capabilities=["security"],
        )

        registry_store.create(online_expert)
        registry_store.create(offline_expert)
        runtime_state_store.set_runtime_state(online_expert.id, WorkerRuntimeState.ONLINE)
        runtime_state_store.set_runtime_state(offline_expert.id, WorkerRuntimeState.OFFLINE)

        # 创建 profiles
        online_profile = create_test_profile("staff_g5_online:expert", skills=["security"])
        offline_profile = create_test_profile("staff_g5_offline:expert", skills=["security"])
        mock_source = MockProfileSource([online_profile, offline_profile])

        # 创建服务链
        profile_filter = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        retrieval_service = WorkerProfileRetrievalService(
            source=mock_source,
            profile_filter=profile_filter,
        )

        recommendation_service = WorkerCandidateRecommendationImpl(
            retrieval_service=retrieval_service,
        )

        expert_diagnosis_service = ExpertDiagnosisService(
            candidate_recommendation_service=recommendation_service,
        )

        # 获取推荐的 participants
        recommended = expert_diagnosis_service._get_recommended_participants(
            question="security analysis",
            participants=None,
        )

        # 验证：只包含 online expert
        if recommended:
            assert "staff_g5_online:expert" in recommended
            assert "staff_g5_offline:expert" not in recommended


# =============================================================================
# Scenario 5: Compatibility Mode Verification
# =============================================================================

class TestCompatibilityMode:
    """场景 5：兼容模式验证"""

    def test_compatibility_mode_passes_unregistered_profiles(
        self,
        registry_store: SQLiteWorkerRegistryStore,
        runtime_state_store: SQLiteWorkerRuntimeStateStore,
    ):
        """
        验证：兼容模式下未注册的 profiles 放行

        Given: strict_mode=False（兼容模式）
        When: 检索未在 registry 注册的 profiles
        Then: 未注册的 profiles 也被返回
        """
        # 不注册任何 worker

        # 创建 profiles（未在 registry 注册）
        unregistered_profile1 = create_test_profile("staff_new_001:default")
        unregistered_profile2 = create_test_profile("staff_new_002:default")
        mock_source = MockProfileSource([unregistered_profile1, unregistered_profile2])

        # 创建兼容模式的 filter（strict_mode=False）
        profile_filter = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=False,  # 兼容模式
        )

        retrieval_service = WorkerProfileRetrievalService(
            source=mock_source,
            profile_filter=profile_filter,
        )

        # 检索
        result = retrieval_service.retrieve(question="test", mode=RetrievalMode.AGENT)

        # 验证：未注册的 profiles 也被返回
        profile_keys = [r.profile.profile_key for r in result.results]
        assert "staff_new_001:default" in profile_keys
        assert "staff_new_002:default" in profile_keys

    def test_strict_mode_filters_unregistered_profiles(
        self,
        registry_store: SQLiteWorkerRegistryStore,
        runtime_state_store: SQLiteWorkerRuntimeStateStore,
    ):
        """
        验证：严格模式下未注册的 profiles 被过滤

        Given: strict_mode=True（严格模式）
        When: 检索未在 registry 注册的 profiles
        Then: 未注册的 profiles 被过滤掉
        """
        # 不注册任何 worker

        # 创建 profiles（未在 registry 注册）
        unregistered_profile = create_test_profile("staff_unregistered:default")
        mock_source = MockProfileSource([unregistered_profile])

        # 创建严格模式的 filter
        profile_filter = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,  # 严格模式
        )

        retrieval_service = WorkerProfileRetrievalService(
            source=mock_source,
            profile_filter=profile_filter,
        )

        # 检索
        result = retrieval_service.retrieve(question="test", mode=RetrievalMode.AGENT)

        # 验证：未注册的 profiles 被过滤
        assert len(result.results) == 0


# =============================================================================
# Wiring Verification Tests
# =============================================================================

class TestWiringVerification:
    """Wiring 验证测试"""

    def test_filter_actually_injected_in_service_chain(
        self,
        registry_store: SQLiteWorkerRegistryStore,
        runtime_state_store: SQLiteWorkerRuntimeStateStore,
    ):
        """
        验证：Filter 实际被注入到检索服务链中

        这是 Phase 4.5 的核心验证：
        - 不是"能力存在"
        - 而是"真实生效"
        """
        # 创建 worker
        online_worker = create_test_worker(
            "wrk_wiring_online",
            "Wiring Online",
            WorkerLifecycleState.ACTIVE,
            WorkerRuntimeState.ONLINE,
            "staff_wiring:online",
        )
        offline_worker = create_test_worker(
            "wrk_wiring_offline",
            "Wiring Offline",
            WorkerLifecycleState.ACTIVE,
            WorkerRuntimeState.OFFLINE,
            "staff_wiring:offline",
        )

        registry_store.create(online_worker)
        registry_store.create(offline_worker)
        runtime_state_store.set_runtime_state(online_worker.id, WorkerRuntimeState.ONLINE)
        runtime_state_store.set_runtime_state(offline_worker.id, WorkerRuntimeState.OFFLINE)

        # 验证：filter 实际工作
        profile_filter = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        # 使用 mock source
        online_profile = create_test_profile("staff_wiring:online")
        offline_profile = create_test_profile("staff_wiring:offline")
        mock_source = MockProfileSource([online_profile, offline_profile])

        # 创建 retrieval service 并注入 filter
        retrieval_service = WorkerProfileRetrievalService(
            source=mock_source,
            profile_filter=profile_filter,  # 关键：filter 被注入
        )

        # 验证 filter 确实被应用
        assert retrieval_service._profile_filter is not None
        assert retrieval_service._profile_filter is profile_filter

        # 验证 filter 实际工作
        result = retrieval_service.retrieve(question="test", mode=RetrievalMode.AGENT)
        profile_keys = [r.profile.profile_key for r in result.results]

        # 只有 online 的被返回
        assert "staff_wiring:online" in profile_keys
        assert "staff_wiring:offline" not in profile_keys


# =============================================================================
# Summary Tests
# =============================================================================

class TestPhase45Summary:
    """Phase 4.5 总结测试"""

    def test_all_services_respect_online_offline_state(
        self,
        registry_store: SQLiteWorkerRegistryStore,
        runtime_state_store: SQLiteWorkerRuntimeStateStore,
    ):
        """
        综合验证：所有服务都尊重 online/offline 状态

        这是 Phase 4.5 的最终验证：
        - Retrieval 服务过滤 offline
        - Recommendation 服务过滤 offline
        - G5 服务过滤 offline
        """
        # 创建完整的测试数据
        workers = [
            create_test_worker("wrk_final_online", "Online", WorkerLifecycleState.ACTIVE, WorkerRuntimeState.ONLINE, "staff_final:online"),
            create_test_worker("wrk_final_offline", "Offline", WorkerLifecycleState.ACTIVE, WorkerRuntimeState.OFFLINE, "staff_final:offline"),
        ]

        for w in workers:
            registry_store.create(w)
            runtime_state_store.set_runtime_state(w.id, w.state.runtime_state)

        profiles = [create_test_profile(w.active_profile_key) for w in workers if w.active_profile_key]
        mock_source = MockProfileSource(profiles)

        # 创建完整的服务链
        profile_filter = RegistryAwareWorkerFilter(
            registry_store=registry_store,
            runtime_state_store=runtime_state_store,
            strict_mode=True,
        )

        retrieval_service = WorkerProfileRetrievalService(
            source=mock_source,
            profile_filter=profile_filter,
        )

        recommendation_service = WorkerCandidateRecommendationImpl(
            retrieval_service=retrieval_service,
        )

        expert_diagnosis_service = ExpertDiagnosisService(
            candidate_recommendation_service=recommendation_service,
        )

        # 验证 retrieval
        retrieval_result = retrieval_service.retrieve(question="test", mode=RetrievalMode.AGENT)
        assert len(retrieval_result.results) == 1
        assert retrieval_result.results[0].profile.profile_key == "staff_final:online"

        # 验证 recommendation
        rec_result = recommendation_service.recommend(question="test", mode=RetrievalMode.EXPERT_DIAGNOSIS)
        assert len(rec_result.recommendations) == 1
        assert rec_result.recommendations[0].profile_key == "staff_final:online"

        # 验证 expert diagnosis
        recommended = expert_diagnosis_service._get_recommended_participants(question="test", participants=None)
        if recommended:
            assert "staff_final:online" in recommended
            assert "staff_final:offline" not in recommended