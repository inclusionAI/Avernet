"""
[集成测试] Fusion Participant Availability

测试场景覆盖：
1. P1: ParticipantAvailabilityChecker 存储不一致
   - API 创建的 worker (无 profile_binding) 应能被正确识别
   - worker_id 直接作为 participant_id 时应工作正常

2. P2: Online 状态不一致
   - set_online 后状态应持久化到数据库
   - 缓存失效后重新查询应返回正确状态

3. 端到端融合测试
   - API 注册的 workers 应能参与融合
   - perspectives 应包含实际内容而非 skipped

CI 验证命令:
    pytest tests/integration/test_fusion_participant_availability.py -v --tb=short
"""

import pytest
import time
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from src.interfaces.api.app import app
from src.interfaces.api.dependencies import worker_dependencies
from src.application.services.participant_availability_checker import (
    ParticipantAvailabilityChecker,
    ParticipantAvailability,
)
from src.domain.models.worker_runtime_state import WorkerRuntimeState


class TestParticipantAvailabilityCheckerRegistryConsistency:
    """
    [P1 测试] ParticipantAvailabilityChecker 存储不一致问题

    问题：当 worker_id 直接作为 participant_id 时，
    如果没有 profile_binding，会被误判为 "unregistered"。
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """每个测试前重置状态"""
        worker_dependencies.reset_stores()
        import os
        os.environ["WORKER_REGISTRY_DATABASE_MODE"] = "sqlite"
        os.environ["WORKER_REGISTRY_SQLITE_DB_PATH"] = ":memory:"
        os.environ["ENABLE_EXPLICIT_PARTICIPANT_AVAILABILITY_WARNING"] = "true"
        yield
        worker_dependencies.reset_stores()

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_worker_without_profile_binding_should_be_recognized(self, client):
        """
        [P1-场景A] API 创建的 worker 无 profile_binding，应被正确识别为已注册

        步骤：
        1. 通过 API 创建 worker（不提供 profile_key）
        2. 验证 worker 存在于 registry
        3. 验证 ProfileBinding 不存在
        4. ParticipantAvailabilityChecker 应识别 worker_id 为已注册
        """
        worker_id = "wrk_p1_test_no_binding"
        worker_data = {
            "id": worker_id,
            "type": "bot",
            "name": "Test Bot No Binding",
            "handle": "@test-no-binding",
            "responsibilities": ["testing"],
            "capabilities": [{"name": "testing", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted",
            # 注意：不提供 profile_key
        }

        # 1. 创建 worker
        resp = client.post("/v1/workers", json=worker_data)
        assert resp.status_code == 201

        # 2. 验证 worker 存在
        get_resp = client.get(f"/v1/workers/{worker_id}")
        assert get_resp.status_code == 200

        # 3. 设置 online
        online_resp = client.put(f"/v1/workers/{worker_id}/online")
        assert online_resp.status_code == 200

        # 4. 使用 ParticipantAvailabilityChecker 检查
        from src.interfaces.api.dependencies.worker_dependencies import (
            _get_registry_store,
            _get_runtime_state_store,
            _get_profile_binding_store,
        )

        checker = ParticipantAvailabilityChecker(
            profile_binding_store=_get_profile_binding_store(),
            runtime_state_store=_get_runtime_state_store(),
            registry_store=_get_registry_store(),  # 修复 P1 Bug：添加 registry_store
        )

        # 核心验证：worker_id 直接作为 participant_id 应被识别
        availability = checker.check_availability(worker_id)

        # 期望结果：已注册且可用
        assert availability.is_registered == True, (
            f"Worker {worker_id} should be registered. "
            f"Got: is_registered={availability.is_registered}, "
            f"reason={availability.unavailability_reason}"
        )
        assert availability.is_available == True, (
            f"Worker {worker_id} should be available (online). "
            f"Got runtime_state={availability.runtime_state}"
        )

    def test_profile_key_participant_resolution(self, client):
        """
        [P1-场景B] profile_key 解析为 worker_id 的场景

        当使用 profile_key 作为 participant_id 时，应能正确反查 worker。
        """
        worker_id = "wrk_p1_test_with_profile"
        profile_key = "profile://test/bot"
        worker_data = {
            "id": worker_id,
            "type": "bot",
            "name": "Test Bot With Profile",
            "handle": "@test-with-profile",
            "responsibilities": ["testing"],
            "capabilities": [{"name": "testing", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted",
            "profile_key": profile_key,  # 提供了 profile_key
        }

        # 创建 worker
        resp = client.post("/v1/workers", json=worker_data)
        assert resp.status_code == 201

        # 设置 online
        client.put(f"/v1/workers/{worker_id}/online")

        from src.interfaces.api.dependencies.worker_dependencies import (
            _get_profile_binding_store,
            _get_runtime_state_store,
        )

        checker = ParticipantAvailabilityChecker(
            profile_binding_store=_get_profile_binding_store(),
            runtime_state_store=_get_runtime_state_store(),
        )

        # 使用 profile_key 检查
        availability = checker.check_availability(profile_key)

        assert availability.is_registered == True
        assert availability.worker_id == worker_id


class TestOnlineStatePersistenceAndConsistency:
    """
    [P2 测试] Online 状态一致性问题

    问题：set_online 返回 online，但立即 GET 可能返回 offline。
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        worker_dependencies.reset_stores()
        import os
        os.environ["CACHE_ENABLED"] = "true"
        os.environ["WORKER_REGISTRY_DATABASE_MODE"] = "sqlite"
        os.environ["WORKER_REGISTRY_SQLITE_DB_PATH"] = ":memory:"
        yield
        worker_dependencies.reset_stores()

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_online_state_persists_after_set_online(self, client):
        """
        [P2-场景A] set_online 后状态应持久化

        步骤：
        1. 创建 worker
        2. 调用 set_online
        3. 立即 GET 验证状态
        4. 再次 GET 验证状态（测试缓存）
        """
        worker_id = "wrk_p2_online_persist"
        worker_data = {
            "id": worker_id,
            "type": "bot",
            "name": "Online Persist Test",
            "handle": "@online-persist",
            "responsibilities": ["testing"],
            "capabilities": [{"name": "testing", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted",
        }

        # 1. 创建 worker
        resp = client.post("/v1/workers", json=worker_data)
        assert resp.status_code == 201

        # 2. 设置 online
        online_resp = client.put(f"/v1/workers/{worker_id}/online")
        assert online_resp.status_code == 200
        assert online_resp.json()["runtime_state"] == "online"

        # 3. 立即验证（第一次 GET）
        get_resp1 = client.get(f"/v1/workers/{worker_id}")
        assert get_resp1.status_code == 200
        assert get_resp1.json()["runtime_state"] == "online", (
            f"[P2 BUG] First GET after set_online returned: "
            f"{get_resp1.json()['runtime_state']}"
        )

        # 4. 再次验证（从缓存读取）
        get_resp2 = client.get(f"/v1/workers/{worker_id}")
        assert get_resp2.status_code == 200
        assert get_resp2.json()["runtime_state"] == "online", (
            f"[P2 BUG] Second GET (cached) returned: "
            f"{get_resp2.json()['runtime_state']}"
        )

    def test_online_state_batch_consistency(self, client):
        """
        [P2-场景B] 批量设置 online 后状态应全部正确

        模拟测试报告中发现的问题模式：
        - 批量设置 online
        - 验证每个 worker 的状态
        """
        worker_ids = [f"wrk_p2_batch_{i:03d}" for i in range(5)]

        # 创建所有 workers
        for wid in worker_ids:
            resp = client.post("/v1/workers", json={
                "id": wid,
                "type": "bot",
                "name": f"Batch Test {wid}",
                "handle": f"@{wid}",
                "responsibilities": ["testing"],
                "capabilities": [{"name": "testing", "level": "expert"}],
                "availability": "available",
                "trust_level": "trusted",
            })
            assert resp.status_code == 201

        # 批量设置 online
        for wid in worker_ids:
            resp = client.put(f"/v1/workers/{wid}/online")
            assert resp.status_code == 200
            assert resp.json()["runtime_state"] == "online"

        # 验证所有 workers 状态
        failures = []
        for wid in worker_ids:
            resp = client.get(f"/v1/workers/{wid}")
            if resp.json()["runtime_state"] != "online":
                failures.append({
                    "worker_id": wid,
                    "actual_state": resp.json()["runtime_state"],
                })

        assert len(failures) == 0, (
            f"[P2 BUG] {len(failures)} workers had inconsistent state: {failures}"
        )

    def test_online_state_with_get_before_set(self, client):
        """
        [P2-场景C] 先 GET 再 set_online 再 GET（模拟测试报告场景）

        这个测试模拟测试报告中的完整流程：
        1. GET 不存在的 worker → 触发 null cache (P0 bug 已修复)
        2. POST 创建 worker
        3. GET 验证创建
        4. PUT online
        5. GET 验证 online
        """
        worker_id = "wrk_p2_full_flow"
        worker_data = {
            "id": worker_id,
            "type": "bot",
            "name": "Full Flow Test",
            "handle": "@full-flow",
            "responsibilities": ["testing"],
            "capabilities": [{"name": "testing", "level": "expert"}],
            "availability": "available",
            "trust_level": "trusted",
        }

        # Step 1: GET 不存在的 worker → 404
        resp1 = client.get(f"/v1/workers/{worker_id}")
        assert resp1.status_code == 404

        # Step 2: POST 创建
        resp2 = client.post("/v1/workers", json=worker_data)
        assert resp2.status_code == 201

        # Step 3: GET 验证创建
        resp3 = client.get(f"/v1/workers/{worker_id}")
        assert resp3.status_code == 200
        assert resp3.json()["runtime_state"] == "offline"

        # Step 4: PUT online
        resp4 = client.put(f"/v1/workers/{worker_id}/online")
        assert resp4.status_code == 200
        assert resp4.json()["runtime_state"] == "online"

        # Step 5: GET 验证 online (关键验证点)
        resp5 = client.get(f"/v1/workers/{worker_id}")
        assert resp5.status_code == 200
        assert resp5.json()["runtime_state"] == "online", (
            f"[P2 BUG] After set_online, GET returned: "
            f"{resp5.json()['runtime_state']}"
        )


class TestFusionEndToEnd:
    """
    [端到端测试] 融合功能完整性测试

    验证 API 注册的 workers 能够正常参与融合。
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        worker_dependencies.reset_stores()
        import os
        os.environ["CACHE_ENABLED"] = "true"
        os.environ["WORKER_REGISTRY_DATABASE_MODE"] = "sqlite"
        os.environ["WORKER_REGISTRY_SQLITE_DB_PATH"] = ":memory:"
        os.environ["ENABLE_EXPLICIT_PARTICIPANT_AVAILABILITY_WARNING"] = "true"
        yield
        worker_dependencies.reset_stores()

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_fusion_with_api_registered_workers(self, client):
        """
        [端到端] API 注册的 workers 应能参与融合

        验证：
        1. 所有 participants 应被识别为 registered
        2. perspectives 应有实际内容，而非 "skipped"
        """
        worker_ids = ["wrk_e2e_aml_001", "wrk_e2e_risk_002", "wrk_e2e_data_003"]

        # 创建 workers
        for wid in worker_ids:
            client.post("/v1/workers", json={
                "id": wid,
                "type": "bot",
                "name": f"E2E Test {wid}",
                "handle": f"@{wid}",
                "responsibilities": ["testing"],
                "capabilities": [{"name": "testing", "level": "expert"}],
                "availability": "available",
                "trust_level": "trusted",
            })
            client.put(f"/v1/workers/{wid}/online")

        # 验证 ParticipantAvailabilityChecker
        from src.interfaces.api.dependencies.worker_dependencies import (
            _get_profile_binding_store,
            _get_runtime_state_store,
            _get_registry_store,
        )

        checker = ParticipantAvailabilityChecker(
            profile_binding_store=_get_profile_binding_store(),
            runtime_state_store=_get_runtime_state_store(),
            registry_store=_get_registry_store(),  # 修复 P1 Bug：添加 registry_store
        )

        # 检查所有 participants
        for wid in worker_ids:
            availability = checker.check_availability(wid)
            assert availability.is_registered == True, (
                f"Participant {wid} should be registered. "
                f"Got: {availability.unavailability_reason}"
            )
            assert availability.is_available == True, (
                f"Participant {wid} should be available. "
                f"Got runtime_state: {availability.runtime_state}"
            )

        # 执行融合（模拟）
        # 注意：实际融合需要 LLM 服务，这里只验证 participant 状态
        # 真实融合测试需要在预发环境进行


class TestParticipantAvailabilityCheckerDirect:
    """
    [单元测试] ParticipantAvailabilityChecker 直接测试

    绕过 API 层，直接测试 checker 的行为。
    """

    def test_checker_should_check_registry_store_first(self):
        """
        [单元] Checker 应优先检查 RegistryStore

        当 participant_id 是一个有效的 worker_id 时，
        应该直接从 RegistryStore 确认，而不是依赖 ProfileBindingStore。
        """
        from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
        from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore
        from src.infra.adapters.in_memory_worker_profile_binding_store import InMemoryWorkerProfileBindingStore
        from src.domain.models.worker import (
            Worker, WorkerType, WorkerIdentity, WorkerState,
            Availability, TrustLevel, Capability, CapabilityLevel,
        )
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState

        # Setup stores
        registry_store = InMemoryWorkerRegistryStore()
        runtime_state_store = InMemoryWorkerRuntimeStateStore()
        profile_binding_store = InMemoryWorkerProfileBindingStore()

        # Create worker WITHOUT profile binding
        worker_id = "wrk_unit_test_001"
        worker = Worker(
            id=worker_id,
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Unit Test", handle="@unit-test"),
            responsibilities=["testing"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(
                availability=Availability.AVAILABLE,
                trust_level=TrustLevel.TRUSTED,
            ),
            lifecycle_state=WorkerLifecycleState.ACTIVE,
        )
        registry_store.create(worker)
        runtime_state_store.set_runtime_state(worker_id, WorkerRuntimeState.ONLINE)

        # Create checker - 添加 registry_store 参数以修复 P1 Bug
        checker = ParticipantAvailabilityChecker(
            profile_binding_store=profile_binding_store,
            runtime_state_store=runtime_state_store,
            registry_store=registry_store,  # 修复：传入 registry_store
        )

        # Check availability - 修复后应返回正确的注册状态
        availability = checker.check_availability(worker_id)

        # 修复后的预期行为: is_registered=True
        assert availability.is_registered == True, (
            f"Worker {worker_id} exists in RegistryStore but "
            f"ParticipantAvailabilityChecker returns is_registered=False. "
            f"Reason: {availability.unavailability_reason}"
        )


__all__ = [
    "TestParticipantAvailabilityCheckerRegistryConsistency",
    "TestOnlineStatePersistenceAndConsistency",
    "TestFusionEndToEnd",
    "TestParticipantAvailabilityCheckerDirect",
]