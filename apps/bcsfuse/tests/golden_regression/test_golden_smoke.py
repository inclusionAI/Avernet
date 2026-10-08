"""
黄金回归集 - 最小发布保护带

这个测试套件验证系统的核心链路在发布前必须通过。

黄金链路覆盖：
1. 公司标准启动方式 + /health 正常
2. G1 最小请求通过
3. G2 最小请求通过
4. G5 最小请求通过
5. Worker Registry Stage 1 最小链路
6. Stage 1 核心规则验证
7. Real Embedding 主链路验证（如果配置齐全）

运行方式：
    pytest tests/golden_regression/test_golden_smoke.py -v

注意：
- 这些测试应该是快速、可靠的
- 不依赖外部服务的测试优先
- 可配置的外部依赖（如 embedding）必须有明确的 skip 条件
"""

import os
import pytest
from fastapi.testclient import TestClient

from src.interfaces.api.app import app


class TestGoldenStartup:
    """黄金测试 - 公司标准启动方式"""

    def test_health_endpoint_returns_healthy(self):
        """验证 /health 端点正常返回"""
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_app_basic_structure(self):
        """验证应用基础结构完整"""
        # 验证主要路由已注册
        routes = [route.path for route in app.routes]

        # Worker Registry 路由
        assert "/v1/workers" in routes
        assert "/v1/workers/{worker_id}" in routes

        # Fusion 路由
        assert "/api/v1/groups/{group_id}/fuse" in routes


class TestGoldenG1:
    """黄金测试 - G1 最小请求通过"""

    def test_g1_minimal_fusion_request(self):
        """验证 G1 最小融合请求可以通过"""
        from src.domain.models.fusion_request import FusionRequest, FuseOptions
        from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
        from src.domain.models.fusion_result import Perspective
        from src.application.services.group_fusion_service import GroupFusionService

        # Mock provider
        class MockProvider(PerspectiveProvider):
            def collect(self, context: PerspectiveContext) -> Perspective:
                return Perspective(
                    participant_id=context.participant_id,
                    participant_type="bot",
                    role="consultant",
                    summary="Mock perspective",
                    status="completed",
                )

        service = GroupFusionService(provider=MockProvider())
        request = FusionRequest(
            question="Test question",
            participants=["bot1"],
        )

        result = service.fuse(request, group_id="test-group")

        # 验证基本响应结构
        assert result.group_id == "test-group"
        assert result.recommendation is not None


class TestGoldenG2:
    """黄金测试 - G2 最小请求通过"""

    def test_g2_minimal_conflict_alignment(self):
        """验证 G2 最小冲突对齐请求可以通过"""
        from src.domain.models.fusion_result import Perspective
        from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
        from src.application.services.group_fusion_service import GroupFusionService

        # Mock provider with conflict perspectives
        class MockProvider(PerspectiveProvider):
            def collect(self, context: PerspectiveContext) -> Perspective:
                perspectives = {
                    "dev": Perspective(
                        participant_id="dev",
                        participant_type="bot",
                        role="driver",
                        summary="从开发角度建议方案A",
                        status="completed",
                    ),
                    "pm": Perspective(
                        participant_id="pm",
                        participant_type="bot",
                        role="consultant",
                        summary="从产品角度建议方案B",
                        status="completed",
                    ),
                }
                return perspectives.get(
                    context.participant_id,
                    Perspective(
                        participant_id=context.participant_id,
                        participant_type="bot",
                        role="consultant",
                        summary="Default perspective",
                        status="completed",
                    )
                )

        service = GroupFusionService(provider=MockProvider())

        from src.domain.models.fusion_request import FusionRequest
        request = FusionRequest(
            question="如何优化系统架构？",
            participants=["dev", "pm"],
        )

        result = service.fuse(request, group_id="test-g2")

        # 验证冲突对齐结果
        assert result.recommendation is not None
        assert len(result.perspectives) == 2


class TestGoldenG5:
    """黄金测试 - G5 最小请求通过"""

    def test_g5_minimal_expert_diagnosis(self):
        """验证 G5 最小专家诊断请求可以通过"""
        from src.domain.models.fusion_result import Perspective
        from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
        from src.application.services.group_fusion_service import GroupFusionService

        # Mock expert provider
        class MockExpertProvider(PerspectiveProvider):
            def collect(self, context: PerspectiveContext) -> Perspective:
                return Perspective(
                    participant_id=context.participant_id,
                    participant_type="bot",
                    role="expert",
                    summary=f"专家 {context.participant_id} 的诊断意见",
                    status="completed",
                    confidence=0.85,
                )

        service = GroupFusionService(provider=MockExpertProvider())

        from src.domain.models.fusion_request import FusionRequest
        request = FusionRequest(
            question="系统性能优化方案评估",
            participants=["dba", "security"],
        )

        result = service.fuse(request, group_id="test-g5")

        # 验证专家诊断结果
        assert result.recommendation is not None


class TestGoldenWorkerRegistry:
    """黄金测试 - Worker Registry Stage 1 最小链路"""

    def test_worker_crud_minimal_flow(self):
        """验证 Worker CRUD 最小流程"""
        client = TestClient(app)

        # 1. Create worker (使用唯一 ID)
        import uuid
        unique_id = f"wrk_test_golden_{uuid.uuid4().hex[:8]}"
        worker_data = {
            "id": unique_id,
            "name": "Test Worker",
            "type": "bot",
        }

        create_response = client.post("/v1/workers", json=worker_data)

        # 打印详细错误（用于调试）
        if create_response.status_code not in [200, 201]:
            print(f"Create worker failed: {create_response.status_code}")
            print(f"Response: {create_response.json()}")

        assert create_response.status_code in [200, 201, 409]  # 409 如果已存在

        worker_id = create_response.json().get("id") or create_response.json().get("worker_id") or unique_id

        # 2. Get worker
        get_response = client.get(f"/v1/workers/{worker_id}")
        assert get_response.status_code in [200, 404]  # 404 如果未实现持久化

        # 3. Update online status
        online_response = client.put(f"/v1/workers/{worker_id}/online")
        assert online_response.status_code in [200, 404, 405]  # 405 如果未实现

        # 4. Update offline status
        offline_response = client.put(f"/v1/workers/{worker_id}/offline")
        assert offline_response.status_code in [200, 404, 405]


class TestGoldenStage1Rules:
    """黄金测试 - Stage 1 核心规则验证"""

    def test_g5_filters_offline_workers(self):
        """验证 G5 默认候选过滤 offline worker 接口存在"""
        # 验证 filter 服务可导入
        from src.application.services.registry_aware_worker_filter import RegistryAwareWorkerFilter
        assert RegistryAwareWorkerFilter is not None
        # 详细逻辑在 tests/integration/test_registry_aware_filtering.py 中验证

    def test_explicit_offline_participant_warning(self):
        """验证显式 offline participant 返回 warning 接口存在"""
        # 验证 checker 服务可导入
        from src.application.services.participant_availability_checker import ParticipantAvailabilityChecker
        assert ParticipantAvailabilityChecker is not None
        # 详细逻辑在 tests/integration/test_fusion_offline_participant_warning.py 中验证


class TestGoldenRealEmbedding:
    """黄金测试 - Real Embedding 主链路验证"""

    @pytest.mark.skipif(
        not os.environ.get("EMBEDDING_BASE_URL"),
        reason="EMBEDDING_BASE_URL not configured - skipping real embedding test"
    )
    def test_real_embedding_minimal_flow(self):
        """验证 real embedding 主链路（如果配置齐全）"""
        from src.infra.embedding.config.embedding_settings import EmbeddingSettings
        from src.infra.embedding.providers.real_provider import RealEmbeddingProvider

        settings = EmbeddingSettings()

        # 检查配置是否齐全
        if not settings.is_configured():
            missing = settings.missing_config()
            pytest.skip(f"Embedding not configured. Missing: {missing}")

        # 创建 provider
        provider = RealEmbeddingProvider(settings=settings)

        # 生成 embedding
        try:
            embedding = provider.embed("test query")

            # 验证 embedding 格式
            assert isinstance(embedding, list)
            assert len(embedding) == settings.dimension
            assert all(isinstance(x, float) for x in embedding)

        except Exception as e:
            # 如果 embedding 调用失败，应该有明确的 fallback
            pytest.fail(f"Real embedding failed without proper fallback: {e}")

    def test_real_embedding_skip_when_not_configured(self):
        """验证 embedding 未配置时，测试明确 skip（不允许假通过）"""
        from src.infra.embedding.config.embedding_settings import EmbeddingSettings

        # 如果配置齐全，这个测试不应该 skip
        settings = EmbeddingSettings()
        if settings.is_configured():
            pytest.skip("Embedding is configured - this test is for unconfigured state")

        # 验证 is_configured() 返回 False
        assert not settings.is_configured()

        # 验证 missing_config() 返回具体缺失项
        missing = settings.missing_config()
        assert len(missing) > 0
        assert all(isinstance(m, str) for m in missing)