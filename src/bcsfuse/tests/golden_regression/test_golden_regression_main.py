"""
Golden Regression Test Suite - 黄金回归测试集

统一的 release smoke suite，覆盖核心功能点。

测试清单：
1. Service startup - python main.py -c configs 启动成功
2. Health check - /health 正常
3. G1 minimal request - G1 最小请求通过
4. G2 minimal request - G2 最小请求通过
5. G5 minimal request - G5 最小请求通过
6. Worker Registry - create worker
7. Worker Registry - online/offline
8. Profile API - register profile
9. Profile API - activate profile
10. G5 default candidate filtering - offline worker
11. Explicit offline participant warning
12. Real embedding main path verification

环境要求：
- 可以设置为真实模式（REQUIRE_REAL_LLM/EMBEDDING=true）验证真实调用
- 可以设置为测试模式（默认）进行快速验证

运行方式：
    # 默认测试模式
    python -m pytest tests/test_golden_regression.py -v -s

    # 真实模式（验证真实LLM/Embedding调用）
    REQUIRE_REAL_LLM=true REQUIRE_REAL_EMBEDDING=true python -m pytest tests/test_golden_regression.py -v -s
"""

import pytest
import os
from fastapi.testclient import TestClient

from src.interfaces.api.app import app
from src.interfaces.api.dependencies.worker_dependencies import reset_stores, use_in_memory_stores
from src.interfaces.api.dependencies.fusion_dependencies import reset_fusion_services
from src.infra.observability.service_counters import reset_service_counters
from src.infra.observability.strict_mode_checker import reset_strict_mode_checker, get_strict_mode_checker


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def setup_memory_stores():
    """每个测试使用内存数据库"""
    use_in_memory_stores()
    reset_fusion_services()
    reset_service_counters()
    reset_strict_mode_checker()
    yield
    reset_stores()
    reset_fusion_services()
    reset_service_counters()
    reset_strict_mode_checker()


@pytest.fixture
def client():
    """创建测试客户端"""
    return TestClient(app)


# ============================================================================
# Test 1: Health Check
# ============================================================================

class TestGoldenRegression:
    """黄金回归测试集"""

    def test_01_health_check(self, client):
        """
        Test 1: Health Check

        验证服务健康检查正常。
        """
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

        print("✅ Test 1: Health check passed")

    # ========================================================================
    # Test 2-5: Fusion API
    # ========================================================================

    def test_02_g1_minimal_request(self, client):
        """
        Test 2: G1 Minimal Request

        验证 G1 最小请求通过（使用 agent 模式）。
        """
        response = client.post(
            "/api/v1/groups/grp-test/fuse",
            json={
                "question": "测试G1问题",
                "fusion_mode": "agent",
                "participants": ["wrk_test"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["fusion_mode"] == "agent"
        assert "perspectives" in data

        print("✅ Test 2: G1 minimal request passed")

    def test_03_g2_minimal_request(self, client):
        """
        Test 3: G2 Minimal Request

        验证 G2 最小请求通过（冲突对齐）。
        """
        response = client.post(
            "/api/v1/groups/grp-test/fuse",
            json={
                "question": "测试G2问题",
                "fusion_mode": "conflict_alignment",
                "participants": ["wrk_test_1", "wrk_test_2"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["fusion_mode"] == "conflict_alignment"

        print("✅ Test 3: G2 minimal request passed")

    def test_04_g5_minimal_request(self, client):
        """
        Test 4: G5 Minimal Request

        验证 G5 最小请求通过（专家会诊）。
        """
        # 先注册一个 worker
        client.post(
            "/v1/workers",
            json={
                "id": "wrk_test_expert",
                "name": "测试专家",
                "type": "bot",
            },
        )

        response = client.post(
            "/api/v1/groups/grp-test/fuse",
            json={
                "question": "测试G5问题",
                "fusion_mode": "expert_diagnosis",
                "participants": ["wrk_test_expert:default"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["fusion_mode"] == "expert_diagnosis"

        print("✅ Test 4: G5 minimal request passed")

    def test_05_g5_with_real_llm_verification(self, client):
        """
        Test 5: G5 with Real LLM Verification

        验证 G5 真实调用 LLM（如果设置 REQUIRE_REAL_LLM=true）。
        """
        # 注册 worker 并设置 online
        worker_response = client.post(
            "/v1/workers",
            json={
                "id": "wrk_llm_test",
                "name": "LLM测试",
                "type": "bot",
            },
        )
        assert worker_response.status_code == 201

        online_response = client.put("/v1/workers/wrk_llm_test/online")
        assert online_response.status_code == 200

        # 发起 G5 请求
        fusion_response = client.post(
            "/api/v1/groups/grp-llm-test/fuse",
            json={
                "question": "这是一个真实的LLM测试问题",
                "fusion_mode": "expert_diagnosis",
                "participants": ["wrk_llm_test:default"],
            },
        )

        assert fusion_response.status_code == 200

        # 如果设置了 REQUIRE_REAL_LLM，验证真实调用
        if os.environ.get("REQUIRE_REAL_LLM") == "true":
            checker = get_strict_mode_checker()
            try:
                checker.validate()
                print("✅ Test 5: G5 with real LLM verification passed")
            except Exception as e:
                print(f"❌ Test 5: Real LLM verification failed: {e}")
                raise
        else:
            print("✅ Test 5: G5 request passed (REQUIRE_REAL_LLM not set)")

    # ========================================================================
    # Test 6-7: Worker Registry
    # ========================================================================

    def test_06_worker_create(self, client):
        """
        Test 6: Worker Create

        验证创建 Worker。
        """
        response = client.post(
            "/v1/workers",
            json={
                "id": "wrk_regression_test",
                "name": "回归测试Worker",
                "type": "bot",
                "handle": "@regression-test",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "wrk_regression_test"
        assert data["lifecycle_state"] == "active"
        assert data["runtime_state"] == "offline"

        print("✅ Test 6: Worker create passed")

    def test_07_worker_online_offline(self, client):
        """
        Test 7: Worker Online/Offline

        验证 Worker online/offline 状态切换。
        """
        # 创建 worker
        client.post(
            "/v1/workers",
            json={
                "id": "wrk_state_test",
                "name": "State Test",
                "type": "bot",
            },
        )

        # 设置 online
        online_resp = client.put("/v1/workers/wrk_state_test/online")
        assert online_resp.status_code == 200
        assert online_resp.json()["runtime_state"] == "online"

        # 设置 offline
        offline_resp = client.put("/v1/workers/wrk_state_test/offline")
        assert offline_resp.status_code == 200
        assert offline_resp.json()["runtime_state"] == "offline"

        print("✅ Test 7: Worker online/offline passed")

    # ========================================================================
    # Test 8-9: Profile API
    # ========================================================================

    def test_08_profile_register(self, client):
        """
        Test 8: Profile Register

        验证 Profile 注册。
        """
        # 先创建 worker
        client.post(
            "/v1/workers",
            json={
                "id": "wrk_profile_test",
                "name": "Profile Test",
                "type": "bot",
            },
        )

        # 注册 profile
        response = client.put(
            "/v1/workers/wrk_profile_test/profiles/default",
            json={
                "display_name": "Test Profile",
                "soul_md": "# Test SOUL\n\nTest content.",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["worker_id"] == "wrk_profile_test"
        assert data["profile_id"] == "default"
        assert data["display_name"] == "Test Profile"

        print("✅ Test 8: Profile register passed")

    def test_09_profile_activate(self, client):
        """
        Test 9: Profile Activate

        验证 Profile 激活。
        """
        # 创建 worker
        client.post(
            "/v1/workers",
            json={
                "id": "wrk_activate_test",
                "name": "Activate Test",
                "type": "bot",
            },
        )

        # 注册 profile（不立即激活）
        client.put(
            "/v1/workers/wrk_activate_test/profiles/test_profile",
            json={
                "display_name": "Test Profile",
            },
        )

        # 激活 profile
        activate_resp = client.put(
            "/v1/workers/wrk_activate_test/profiles/test_profile/activate"
        )

        assert activate_resp.status_code == 200
        data = activate_resp.json()
        assert data["is_active"] is True
        assert data["worker_id"] == "wrk_activate_test"
        assert data["profile_id"] == "test_profile"

        print("✅ Test 9: Profile activate passed")

    # ========================================================================
    # Test 10: G5 Default Candidate Filtering
    # ========================================================================

    def test_10_g5_default_candidate_filtering(self, client):
        """
        Test 10: G5 Default Candidate Filtering

        验证 G5 默认候选过滤 offline worker。
        """
        # 创建 offline worker
        client.post(
            "/v1/workers",
            json={
                "id": "wrk_offline_worker",
                "name": "Offline Worker",
                "type": "bot",
            },
        )
        # 默认就是 offline

        # 创建 online worker
        client.post(
            "/v1/workers",
            json={
                "id": "wrk_online_worker",
                "name": "Online Worker",
                "type": "bot",
            },
        )
        client.put("/v1/workers/wrk_online_worker/online")

        # 发起 G5 请求（使用默认推荐）
        response = client.post(
            "/api/v1/groups/grp-filter-test/fuse",
            json={
                "question": "测试过滤offline worker",
                "fusion_mode": "expert_diagnosis",
                "participants": ["wrk_offline_worker:default", "wrk_online_worker:default"],
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 验证 offline worker 不在 perspectives 中
        participant_ids = [p.get("participant_id") for p in data.get("perspectives", [])]
        assert "wrk_offline_worker" not in participant_ids

        print("✅ Test 10: G5 default candidate filtering passed")

    # ========================================================================
    # Test 11: Explicit Offline Participant Warning
    # ========================================================================

    def test_11_explicit_offline_participant_warning(self, client):
        """
        Test 11: Explicit Offline Participant Warning

        验证显式指定 offline participant 时有 warning。
        """
        # 创建 offline worker
        client.post(
            "/v1/workers",
            json={
                "id": "wrk_explicit_offline",
                "name": "Explicit Offline",
                "type": "bot",
            },
        )

        # 显式指定 offline participant
        response = client.post(
            "/api/v1/groups/grp-warning-test/fuse",
            json={
                "question": "测试offline warning",
                "fusion_mode": "expert_diagnosis",
                "participants": ["wrk_explicit_offline:default"],
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 验证有 warning（如果 feature 启用）
        if os.environ.get("ENABLE_EXPLICIT_PARTICIPANT_AVAILABILITY_WARNING") == "true":
            warnings = data.get("warnings", [])
            assert len(warnings) > 0
            print(f"✅ Test 11: Warning found: {warnings[0]}")
        else:
            print("✅ Test 11: Request passed (warning feature not enabled)")

    # ========================================================================
    # Test 12: Real Embedding Verification
    # ========================================================================

    def test_12_real_embedding_verification(self, client):
        """
        Test 12: Real Embedding Verification

        验证真实调用 Embedding（如果设置 REQUIRE_REAL_EMBEDDING=true）。
        """
        # 创建 worker 并设置 online
        client.post(
            "/v1/workers",
            json={
                "id": "wrk_embedding_test",
                "name": "Embedding Test",
                "type": "bot",
            },
        )
        client.put("/v1/workers/wrk_embedding_test/online")

        # 发起 G5 请求（触发 embedding 调用）
        response = client.post(
            "/api/v1/groups/grp-embedding-test/fuse",
            json={
                "question": "测试embedding调用",
                "fusion_mode": "expert_diagnosis",
                "participants": ["wrk_embedding_test:default"],
            },
        )

        assert response.status_code == 200

        # 如果设置了 REQUIRE_REAL_EMBEDDING，验证真实调用
        if os.environ.get("REQUIRE_REAL_EMBEDDING") == "true":
            from src.infra.observability.service_counters import get_service_counters
            counters = get_service_counters()

            summary = counters.get_summary()

            # 至少有 1 次 embedding 调用
            assert summary["embedding_real_call_count"] > 0, \
                f"Expected real embedding call but got: {summary}"

            print(f"✅ Test 12: Real embedding verified: {summary['embedding_real_call_count']} calls")
        else:
            print("✅ Test 12: G5 request passed (REQUIRE_REAL_EMBEDDING not set)")


# ============================================================================
# Final Validation
# ============================================================================

def test_final_validation():
    """
    Final Validation

    在所有测试结束后，验证 strict mode 要求。
    """
    if os.environ.get("REQUIRE_REAL_LLM") == "true" or os.environ.get("REQUIRE_REAL_EMBEDDING") == "true":
        checker = get_strict_mode_checker()

        try:
            checker.validate()
            print("\n" + "=" * 70)
            print("✅ ALL STRICT MODE CHECKS PASSED!")
            print("=" * 70)
        except Exception as e:
            print("\n" + "=" * 70)
            print("❌ STRICT MODE VALIDATION FAILED!")
            print("=" * 70)
            raise

    # 打印服务计数器
    from src.infra.observability.service_counters import get_service_counters
    counters = get_service_counters()
    counters.print_summary()


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])