"""
Integration Tests for Fusion Offline Participant Warning

Stage 1 Phase 5: Fusion Offline Participant Warning

测试场景：
1. G5 显式 offline participant warning
2. G5 显式 online participant 无 warning
3. G5 混合 online/offline participants
4. G1/G2 也支持 warning（因为共用 _collect_perspectives）
5. 默认候选推荐仍保持过滤逻辑（不变）

关键验证：
- 显式 offline participant 不自动替换
- 结果中保留 participant 并标记 status="skipped"
- warnings 中包含清晰的原因说明
"""

import os
import tempfile
import pytest
from datetime import datetime
from unittest.mock import Mock

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
from src.domain.models.fusion_request import FusionRequest, FuseOptions
from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext, Perspective
from src.infra.adapters.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
from src.infra.adapters.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore
from src.infra.adapters.sqlite_worker_profile_binding_store import SQLiteWorkerProfileBindingStore
from src.infra.adapters.sqlite_worker_audit_log_store import SQLiteWorkerAuditLogStore
from src.domain.models.worker_source_info import WorkerSourceType
from src.application.services.participant_availability_checker import (
    ParticipantAvailabilityChecker,
    ParticipantAvailability,
)
from src.application.services.group_fusion_service import GroupFusionService
from src.application.services.expert_diagnosis_service import ExpertDiagnosisService
from src.application.services.worker_candidate_recommendation_impl import WorkerCandidateRecommendationImpl
from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService
from src.application.services.registry_aware_worker_filter import RegistryAwareWorkerFilter
from src.domain.models.worker_profile import WorkerProfile, ProfileType, WorkerProfileScanResult
from src.domain.models.skill_profile import SkillProfile


# =============================================================================
# Test Fixtures
# =============================================================================

class MockPerspectiveProvider(PerspectiveProvider):
    """Mock Perspective Provider for testing"""

    def __init__(self, perspectives: dict[str, Perspective] = None):
        self._perspectives = perspectives or {}
        self._call_log: list[str] = []

    def collect(self, context: PerspectiveContext) -> Perspective:
        self._call_log.append(context.participant_id)
        if context.participant_id in self._perspectives:
            return self._perspectives[context.participant_id]
        # Default: return completed perspective
        return Perspective(
            participant_id=context.participant_id,
            participant_type="bot",
            role="consultant",
            summary=f"Perspective from {context.participant_id}",
            confidence=0.85,
            evidence=[],
            status="completed",
        )

    @property
    def called_participants(self) -> list[str]:
        return self._call_log


def create_test_worker(
    worker_id: str,
    runtime_state: WorkerRuntimeState = WorkerRuntimeState.OFFLINE,
) -> Worker:
    """创建测试用的 Worker"""
    return Worker(
        id=worker_id,
        type=WorkerType.BOT,
        identity=WorkerIdentity(name=f"Test {worker_id}", handle=f"@{worker_id}"),
        responsibilities=["testing"],
        domains=["testing"],
        capabilities=[Capability(name="testing", level=CapabilityLevel.EXPERT)],
        state=WorkerState(
            availability=Availability.AVAILABLE,
            trust_level=TrustLevel.TRUSTED,
            runtime_state=runtime_state,
            lifecycle_state=WorkerLifecycleState.ACTIVE,
        ),
    )


def setup_worker_with_profile(
    stores: dict,
    worker_id: str,
    profile_key: str,
    runtime_state: WorkerRuntimeState,
) -> Worker:
    """创建 Worker 并绑定 profile"""
    from src.application.services.worker_import_service import WorkerImportService
    from src.infra.adapters.in_memory_worker_index_sync_adapter import InMemoryWorkerIndexSyncAdapter

    # 使用 WorkerImportService 创建 worker
    import_service = WorkerImportService(
        registry_store=stores["registry_store"],
        runtime_state_store=stores["runtime_state_store"],
        profile_binding_store=stores["profile_binding_store"],
        audit_log_adapter=stores.get("audit_log_store"),
        index_sync_adapter=InMemoryWorkerIndexSyncAdapter(),
    )

    # 创建 worker（不包含 profile_key，由 import_service 内部处理）
    worker = import_service.import_from_api({
        "id": worker_id,
        "type": "bot",
        "identity": {"name": f"Test {worker_id}", "handle": f"@{worker_id}"},
        "responsibilities": ["testing"],
        "capabilities": [{"name": "testing", "level": "expert"}],
        "state": {
            "availability": "available",
            "trust_level": "trusted",
        },
    })

    # 绑定 profile（import_service 可能已经做了，但我们确保正确绑定）
    stores["profile_binding_store"].bind_profile(
        worker_id, profile_key, WorkerSourceType.API
    )

    # 设置 active_profile_key 字段（用于 RegistryAwareWorkerFilter）
    worker.active_profile_key = profile_key
    stores["registry_store"].update(worker)

    # 设置运行时状态
    stores["runtime_state_store"].set_runtime_state(worker_id, runtime_state)

    return worker


def create_test_profile(profile_key: str, skills: list[str] | None = None) -> WorkerProfile:
    """创建测试用的 WorkerProfile"""
    parts = profile_key.split(":")
    if len(parts) == 2:
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


@pytest.fixture
def temp_db_path():
    """创建临时数据库"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def stores(temp_db_path):
    """创建存储实例"""
    registry_store = SQLiteWorkerRegistryStore(temp_db_path)
    runtime_state_store = SQLiteWorkerRuntimeStateStore(temp_db_path)
    profile_binding_store = SQLiteWorkerProfileBindingStore(temp_db_path)
    audit_log_store = SQLiteWorkerAuditLogStore(temp_db_path)

    yield {
        "registry_store": registry_store,
        "runtime_state_store": runtime_state_store,
        "profile_binding_store": profile_binding_store,
        "audit_log_store": audit_log_store,
    }

    registry_store.close()
    runtime_state_store.close()
    profile_binding_store.close()
    audit_log_store.close()


@pytest.fixture
def availability_checker(stores):
    """创建可用性检查器"""
    return ParticipantAvailabilityChecker(
        profile_binding_store=stores["profile_binding_store"],
        runtime_state_store=stores["runtime_state_store"],
    )


# =============================================================================
# Test: ParticipantAvailabilityChecker
# =============================================================================

class TestParticipantAvailabilityChecker:
    """测试 ParticipantAvailabilityChecker"""

    def test_check_unregistered_participant(self, availability_checker):
        """
        验证：未注册的 participant 返回 is_available=False

        Given: participant_id 没有对应的 worker
        When: 检查可用性
        Then: is_available=False, unavailability_reason="unregistered"
        """
        result = availability_checker.check_availability("staff_unknown:default")

        assert result.is_available is False
        assert result.is_registered is False
        assert result.unavailability_reason == "unregistered"
        assert result.worker_id is None

    def test_check_online_participant(self, stores, availability_checker):
        """
        验证：已注册且 online 的 participant 返回 is_available=True

        Given: 创建 worker 并设置为 online
        When: 检查可用性
        Then: is_available=True
        """
        # 创建 worker 并绑定 profile
        setup_worker_with_profile(
            stores, "wrk_online", "staff_online:default", WorkerRuntimeState.ONLINE
        )

        result = availability_checker.check_availability("staff_online:default")

        assert result.is_available is True
        assert result.is_registered is True
        assert result.worker_id == "wrk_online"
        assert result.runtime_state == WorkerRuntimeState.ONLINE

    def test_check_offline_participant(self, stores, availability_checker):
        """
        验证：已注册但 offline 的 participant 返回 is_available=False

        Given: 创建 worker 但保持 offline
        When: 检查可用性
        Then: is_available=False, unavailability_reason 包含 "offline"
        """
        # 创建 worker 并绑定 profile
        setup_worker_with_profile(
            stores, "wrk_offline", "staff_offline:default", WorkerRuntimeState.OFFLINE
        )

        result = availability_checker.check_availability("staff_offline:default")

        assert result.is_available is False
        assert result.is_registered is True
        assert result.worker_id == "wrk_offline"
        assert result.runtime_state == WorkerRuntimeState.OFFLINE
        assert "offline" in result.unavailability_reason.lower()


# =============================================================================
# Test: G5 Offline Participant Warning
# =============================================================================

class TestG5OfflineParticipantWarning:
    """测试 G5 模式的 offline participant warning"""

    def test_g5_warning_for_explicit_offline_participant(self, stores, availability_checker):
        """
        验证：G5 显式 offline participant 产生 warning

        Given: 创建一个 offline worker
        When: 在 G5 模式中显式指定该 participant
        Then: 返回结果包含 warning，participant status 为 skipped
        """
        # 创建 offline worker 并绑定 profile
        setup_worker_with_profile(
            stores, "wrk_g5_offline", "staff_g5_offline:default", WorkerRuntimeState.OFFLINE
        )

        # 创建服务
        mock_provider = MockPerspectiveProvider()
        fusion_service = GroupFusionService(
            provider=mock_provider,
            availability_checker=availability_checker,
        )

        # 发送请求
        request = FusionRequest(
            question="测试问题",
            participants=["staff_g5_offline:default"],
            fusion_mode="expert_diagnosis",
            options=FuseOptions(strict_participants=False),  # 非严格模式，创建 skipped perspective
        )

        result = fusion_service.fuse(request, group_id="grp-test")

        # 验证
        assert len(result.warnings) > 0
        assert any("offline" in w.lower() for w in result.warnings)
        assert len(result.perspectives) == 1
        assert result.perspectives[0].status == "skipped"
        assert result.perspectives[0].participant_id == "staff_g5_offline:default"
        # 验证 provider 没有被调用（offline participant 跳过收集）
        assert "staff_g5_offline:default" not in mock_provider.called_participants

    def test_g5_no_warning_for_online_participant(self, stores, availability_checker):
        """
        验证：G5 显式 online participant 不产生 warning

        Given: 创建一个 online worker
        When: 在 G5 模式中显式指定该 participant
        Then: 不产生 offline warning
        """
        # 创建 online worker 并绑定 profile
        setup_worker_with_profile(
            stores, "wrk_g5_online", "staff_g5_online:default", WorkerRuntimeState.ONLINE
        )

        # 创建服务
        mock_provider = MockPerspectiveProvider()
        fusion_service = GroupFusionService(
            provider=mock_provider,
            availability_checker=availability_checker,
        )

        # 发送请求
        request = FusionRequest(
            question="测试问题",
            participants=["staff_g5_online:default"],
            fusion_mode="expert_diagnosis",
            options=FuseOptions(strict_participants=False),
        )

        result = fusion_service.fuse(request, group_id="grp-test")

        # 验证：无 offline warning
        assert not any("offline" in w.lower() for w in result.warnings)
        assert result.perspectives[0].status == "completed"

    def test_g5_mixed_online_offline_participants(self, stores, availability_checker):
        """
        验证：G5 混合 online + offline participants

        Given: 创建一个 online 和一个 offline worker
        When: 在 G5 模式中同时指定两个 participants
        Then: online 正常完成，offline 被标记 warning 和 skipped
        """
        # 创建 online worker
        setup_worker_with_profile(
            stores, "wrk_mixed_online", "staff_mixed_online:default", WorkerRuntimeState.ONLINE
        )

        # 创建 offline worker
        setup_worker_with_profile(
            stores, "wrk_mixed_offline", "staff_mixed_offline:default", WorkerRuntimeState.OFFLINE
        )

        # 创建服务
        mock_provider = MockPerspectiveProvider()
        fusion_service = GroupFusionService(
            provider=mock_provider,
            availability_checker=availability_checker,
        )

        # 发送请求
        request = FusionRequest(
            question="测试问题",
            participants=["staff_mixed_online:default", "staff_mixed_offline:default"],
            fusion_mode="expert_diagnosis",
            options=FuseOptions(strict_participants=False),  # 非严格模式
        )

        result = fusion_service.fuse(request, group_id="grp-test")

        # 验证
        assert len(result.perspectives) == 2

        # 找到 online 和 offline 的 perspectives
        online_p = next(p for p in result.perspectives if p.participant_id == "staff_mixed_online:default")
        offline_p = next(p for p in result.perspectives if p.participant_id == "staff_mixed_offline:default")

        assert online_p.status == "completed"
        assert offline_p.status == "skipped"

        # 验证 warning
        assert any("offline" in w.lower() and "staff_mixed_offline" in w for w in result.warnings)

        # 验证 partial success
        assert result.partial_success is True

    def test_offline_explicit_participant_not_auto_replaced(self, stores, availability_checker):
        """
        验证：显式 offline participant 不被自动替换

        Given: 创建一个 offline worker
        When: 在请求中显式指定该 participant
        Then: 结果中保留该 participant，不会被替换成其他 worker
        """
        # 创建 offline worker 并绑定 profile
        setup_worker_with_profile(
            stores, "wrk_no_replace", "staff_no_replace:default", WorkerRuntimeState.OFFLINE
        )

        # 创建服务
        mock_provider = MockPerspectiveProvider()
        fusion_service = GroupFusionService(
            provider=mock_provider,
            availability_checker=availability_checker,
        )

        # 发送请求
        request = FusionRequest(
            question="测试问题",
            participants=["staff_no_replace:default"],
            fusion_mode="expert_diagnosis",
            options=FuseOptions(strict_participants=False),  # 非严格模式
        )

        result = fusion_service.fuse(request, group_id="grp-test")

        # 验证：participant 被保留在结果中
        assert len(result.perspectives) == 1
        assert result.perspectives[0].participant_id == "staff_no_replace:default"
        # 验证：没有被替换成其他 participant
        assert "staff_no_replace:default" in result.perspectives[0].participant_id


# =============================================================================
# Test: G1/G2 Offline Participant Warning
# =============================================================================

class TestG1G2OfflineParticipantWarning:
    """测试 G1/G2 模式的 offline participant warning"""

    def test_g1_warning_for_explicit_offline_participant(self, stores, availability_checker):
        """
        验证：G1 显式 offline participant 也产生 warning

        Given: 创建一个 offline worker
        When: 在 G1 模式中显式指定该 participant
        Then: 返回结果包含 warning
        """
        # 创建 offline worker 并绑定 profile
        setup_worker_with_profile(
            stores, "wrk_g1_offline", "staff_g1_offline:default", WorkerRuntimeState.OFFLINE
        )

        # 创建服务
        mock_provider = MockPerspectiveProvider()
        fusion_service = GroupFusionService(
            provider=mock_provider,
            availability_checker=availability_checker,
        )

        # 发送请求（G1 模式）
        request = FusionRequest(
            question="测试问题",
            participants=["staff_g1_offline:default"],
            fusion_mode="agent",  # G1 模式
            options=FuseOptions(strict_participants=False),  # 非严格模式，创建 skipped perspective
        )

        result = fusion_service.fuse(request, group_id="grp-test")

        # 验证
        assert len(result.warnings) > 0
        assert any("offline" in w.lower() for w in result.warnings)
        assert result.perspectives[0].status == "skipped"

    def test_g2_warning_for_explicit_offline_participant(self, stores, availability_checker):
        """
        验证：G2 显式 offline participant 也产生 warning

        Given: 创建一个 offline worker
        When: 在 G2 模式中显式指定该 participant
        Then: 返回结果包含 warning
        """
        # 创建 offline worker 并绑定 profile
        setup_worker_with_profile(
            stores, "wrk_g2_offline", "staff_g2_offline:default", WorkerRuntimeState.OFFLINE
        )

        # 创建服务
        mock_provider = MockPerspectiveProvider()
        fusion_service = GroupFusionService(
            provider=mock_provider,
            availability_checker=availability_checker,
        )

        # 发送请求（G2 模式）
        request = FusionRequest(
            question="测试问题",
            participants=["staff_g2_offline:default"],
            fusion_mode="conflict_alignment",  # G2 模式
            options=FuseOptions(strict_participants=False),  # 非严格模式，创建 skipped perspective
        )

        result = fusion_service.fuse(request, group_id="grp-test")

        # 验证
        assert len(result.warnings) > 0
        assert any("offline" in w.lower() for w in result.warnings)
        assert result.perspectives[0].status == "skipped"


# =============================================================================
# Test: Default Candidate Filtering Still Works
# =============================================================================

class TestDefaultCandidateFilteringStillWorks:
    """验证默认候选过滤逻辑不被破坏"""

    def test_default_candidate_filtering_still_works(self, stores):
        """
        验证：默认候选推荐仍保持过滤逻辑

        Given: 创建 online 和 offline workers
        When: 使用 retrieval service 查询（不带显式 participants）
        Then: 只有 online worker 的 profile 被返回
        """
        # 创建 online worker
        setup_worker_with_profile(
            stores, "wrk_filter_online", "staff_filter_online:default", WorkerRuntimeState.ONLINE
        )

        # 创建 offline worker
        setup_worker_with_profile(
            stores, "wrk_filter_offline", "staff_filter_offline:default", WorkerRuntimeState.OFFLINE
        )

        # 创建 filter 和 retrieval service
        profile_filter = RegistryAwareWorkerFilter(
            registry_store=stores["registry_store"],
            runtime_state_store=stores["runtime_state_store"],
            strict_mode=True,
        )

        # 创建 mock source
        online_profile = create_test_profile("staff_filter_online:default", ["testing"])
        offline_profile = create_test_profile("staff_filter_offline:default", ["testing"])

        class MockSource:
            def scan(self):
                return WorkerProfileScanResult(
                    profiles=[online_profile, offline_profile],
                    scan_warnings=[],
                    source_roots=["mock"],
                )

        retrieval_service = WorkerProfileRetrievalService(
            source=MockSource(),
            profile_filter=profile_filter,
        )

        # 检索
        from src.domain.models.retrieval_mode import RetrievalMode
        result = retrieval_service.retrieve(question="testing", mode=RetrievalMode.AGENT)

        # 验证：只有 online 的 profile
        profile_keys = [r.profile.profile_key for r in result.results]
        assert "staff_filter_online:default" in profile_keys
        assert "staff_filter_offline:default" not in profile_keys


# =============================================================================
# Test: Unregistered Participant Warning
# =============================================================================

class TestUnregisteredParticipantWarning:
    """测试未注册 participant 的 warning"""

    def test_unregistered_participant_warning(self, availability_checker):
        """
        验证：未注册的 participant 产生 "unregistered" warning

        Given: 一个没有对应 worker 的 participant_id
        When: 在请求中指定该 participant
        Then: 产生 "not registered" warning
        """
        # 创建服务
        mock_provider = MockPerspectiveProvider()
        fusion_service = GroupFusionService(
            provider=mock_provider,
            availability_checker=availability_checker,
        )

        # 发送请求
        request = FusionRequest(
            question="测试问题",
            participants=["staff_unregistered:default"],
            fusion_mode="agent",
            options=FuseOptions(strict_participants=False),  # 非严格模式，创建 skipped perspective
        )

        result = fusion_service.fuse(request, group_id="grp-test")

        # 验证：warning 包含 "not registered"
        assert len(result.warnings) > 0
        assert any("not registered" in w.lower() or "unregistered" in w.lower() for w in result.warnings)
        assert result.perspectives[0].status == "skipped"


# =============================================================================
# Summary Test
# =============================================================================

class TestPhase5Summary:
    """Phase 5 总结验证"""

    def test_two_rules_both_implemented(self, stores, availability_checker):
        """
        验证：Phase 5 完成后，两条规则都已实现

        规则 1：默认候选过滤 offline worker（Phase 4.5 已实现）
        规则 2：显式 offline participant warning（Phase 5 实现）

        Given: 创建 online 和 offline workers
        When: 同时测试两种场景
        Then: 两条规则都生效
        """
        # 创建 online worker
        setup_worker_with_profile(
            stores, "wrk_summary_online", "staff_summary_online:default", WorkerRuntimeState.ONLINE
        )

        # 创建 offline worker
        setup_worker_with_profile(
            stores, "wrk_summary_offline", "staff_summary_offline:default", WorkerRuntimeState.OFFLINE
        )

        # 规则 1：默认候选过滤
        profile_filter = RegistryAwareWorkerFilter(
            registry_store=stores["registry_store"],
            runtime_state_store=stores["runtime_state_store"],
            strict_mode=True,
        )
        allowed_keys = profile_filter.get_allowed_profile_keys()
        assert "staff_summary_online:default" in allowed_keys
        assert "staff_summary_offline:default" not in allowed_keys

        # 规则 2：显式 offline participant warning
        mock_provider = MockPerspectiveProvider()
        fusion_service = GroupFusionService(
            provider=mock_provider,
            availability_checker=availability_checker,
        )

        request = FusionRequest(
            question="测试问题",
            participants=["staff_summary_offline:default"],
            fusion_mode="expert_diagnosis",
            options=FuseOptions(strict_participants=False),  # 非严格模式，创建 skipped perspective
        )

        result = fusion_service.fuse(request, group_id="grp-test")

        # 验证 warning
        assert any("offline" in w.lower() for w in result.warnings)
        assert result.perspectives[0].status == "skipped"

        # ✅ Phase 5 验证完成
        # 两条规则均已实现：
        # 1. 默认候选过滤 offline worker ✅
        # 2. 显式 offline participant warning ✅