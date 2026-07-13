"""
真实 LLM/Embedding 集成测试

使用真实的 LLM Gateway 和 Embedding API 进行端到端测试。
不使用 mock，验证完整的数据流路。

前置条件：
- .env.fusion.real 中配置了正确的 LLM_BASE_URL 和 LLM_AUTH_TOKEN
- .env.fusion.real 中配置了正确的 EMBEDDING_BASE_URL 和 EMBEDDING_AUTH_TOKEN
- 本地服务已启动 (http://127.0.0.1:8765)

运行方式：
    pytest tests/integration/test_real_llm_embedding.py -v --tb=short
"""

import os
import time

import pytest
import requests


# =============================================================================
# 配置
# =============================================================================

BASE_URL = os.environ.get("SERVICE_URL", "http://127.0.0.1:8765")
FUSION_API_PREFIX = "/api/v1"
WORKER_API_PREFIX = "/v1"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def ensure_service_running():
    """确保服务正在运行"""
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=5)
        if resp.status_code != 200:
            pytest.skip("Service not running")
    except requests.exceptions.RequestException:
        pytest.skip("Service not running")


@pytest.fixture(scope="module")
def test_workers():
    """创建测试用的 workers 并设置 online"""
    workers = [
        {"id": "wrk_real_llm_test_1", "name": "Real LLM Test 1", "type": "bot"},
        {"id": "wrk_real_llm_test_2", "name": "Real LLM Test 2", "type": "bot"},
        {"id": "wrk_real_llm_expert", "name": "Real LLM Expert", "type": "bot"},
    ]

    created = []
    for w in workers:
        # 创建 worker
        requests.post(f"{BASE_URL}{WORKER_API_PREFIX}/workers", json=w)

        # 设置 online
        requests.put(f"{BASE_URL}{WORKER_API_PREFIX}/workers/{w['id']}/online")

        # 创建 profile binding
        profile_data = {
            "capabilities": [{"name": "testing", "level": "expert"}],
            "skill_sets": [{"name": "backend", "skills": ["python", "architecture"]}],
        }
        requests.put(
            f"{BASE_URL}{WORKER_API_PREFIX}/workers/{w['id']}/profiles/default",
            json=profile_data,
        )

        # 激活 profile
        requests.put(
            f"{BASE_URL}{WORKER_API_PREFIX}/workers/{w['id']}/profiles/default/activate"
        )

        created.append(f"{w['id']}:default")

    yield created

    # 清理（可选）


# =============================================================================
# Tests
# =============================================================================


class TestRealEmbedding:
    """真实 Embedding API 测试"""

    def test_embedding_provider_connection(self, ensure_service_running):
        """测试 Embedding Provider 连接"""
        from src.infra.embedding.config.embedding_settings import EmbeddingSettings
        from src.infra.embedding.providers.real_provider import RealEmbeddingProvider

        settings = EmbeddingSettings()

        if not settings.is_configured():
            pytest.skip("Embedding not configured")

        provider = RealEmbeddingProvider(settings=settings)

        # 测试单个文本 embedding
        test_text = "这是一个测试文本，用于验证 Embedding API 连接。"
        vector = provider.embed(test_text)

        assert vector is not None
        assert len(vector) == settings.dimension
        assert all(isinstance(v, float) for v in vector)

    def test_embedding_batch(self, ensure_service_running):
        """测试批量 Embedding"""
        from src.infra.embedding.config.embedding_settings import EmbeddingSettings
        from src.infra.embedding.providers.real_provider import RealEmbeddingProvider

        settings = EmbeddingSettings()

        if not settings.is_configured():
            pytest.skip("Embedding not configured")

        provider = RealEmbeddingProvider(settings=settings)

        # 测试批量 embedding
        texts = [
            "第一个测试文本",
            "第二个测试文本",
            "第三个测试文本",
        ]
        vectors = provider.embed_batch(texts)

        assert len(vectors) == 3
        for v in vectors:
            assert len(v) == settings.dimension


class TestRealLLM:
    """真实 LLM API 测试"""

    def test_g1_with_real_llm(self, ensure_service_running, test_workers):
        """G1 模式使用真实 LLM"""
        participant = test_workers[0]

        payload = {
            "question": "请分析微服务架构的优缺点，并给出使用建议。",
            "participants": [participant],
        }

        resp = requests.post(
            f"{BASE_URL}{FUSION_API_PREFIX}/groups/grp-real-test-g1/fuse",
            json=payload,
            timeout=120,
        )

        assert resp.status_code == 200
        data = resp.json()

        # 验证基本结构
        assert data["fusion_mode"] == "agent"
        assert len(data["perspectives"]) >= 1
        assert data["recommendation"] is not None

        # 验证 perspectives 内容（真实 LLM 应该生成有意义的内容）
        for p in data["perspectives"]:
            if p["status"] == "completed":
                assert len(p.get("summary", "")) > 10, "Summary should have content"
                assert p.get("confidence", 0) > 0, "Should have confidence score"

    def test_g2_with_real_llm(self, ensure_service_running, test_workers):
        """G2 模式使用真实 LLM"""
        participants = test_workers[:2]

        payload = {
            "question": "我们应该使用哪种数据库：PostgreSQL 还是 MySQL？请从不同角度分析。",
            "participants": participants,
            "fusion_mode": "conflict_alignment",
        }

        resp = requests.post(
            f"{BASE_URL}{FUSION_API_PREFIX}/groups/grp-real-test-g2/fuse",
            json=payload,
            timeout=120,
        )

        assert resp.status_code == 200
        data = resp.json()

        # 验证基本结构
        assert data["fusion_mode"] == "conflict_alignment"
        assert len(data["perspectives"]) >= 2
        assert "conflicts" in data
        assert "alignment_points" in data
        assert "structured_conflict_analysis" in data

    def test_g5_with_real_llm(self, ensure_service_running, test_workers):
        """G5 模式使用真实 LLM"""
        participant = test_workers[2]

        payload = {
            "question": "生产环境数据库迁移风险评估",
            "participants": [participant],
            "fusion_mode": "expert_diagnosis",
        }

        resp = requests.post(
            f"{BASE_URL}{FUSION_API_PREFIX}/groups/grp-real-test-g5/fuse",
            json=payload,
            timeout=120,
        )

        assert resp.status_code == 200
        data = resp.json()

        # 验证基本结构
        assert data["fusion_mode"] == "expert_diagnosis"
        assert data["risk_assessment"] is not None
        assert len(data["critical_issues"]) >= 0
        assert len(data["recommendations"]) >= 0
        assert "structured_risk" in data

        # 验证 risk_assessment 结构
        assert "overall" in data["risk_assessment"]
        assert "categories" in data["risk_assessment"]


class TestVectorStoreWithRealEmbedding:
    """Vector Store 与真实 Embedding 集成测试"""

    def test_vector_store_persistence(self, ensure_service_running):
        """测试 Vector Store 持久化"""
        import tempfile
        from pathlib import Path

        from src.domain.models.vector_point import VectorPoint
        from src.infra.embedding.config.embedding_settings import EmbeddingSettings
        from src.infra.embedding.providers.real_provider import RealEmbeddingProvider
        from src.infra.vectorstores.faiss_sqlite_vector_store import FaissSqliteVectorStore

        settings = EmbeddingSettings()

        if not settings.is_configured():
            pytest.skip("Embedding not configured")

        provider = RealEmbeddingProvider(settings=settings)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_vector.db"

            # 创建 store
            store = FaissSqliteVectorStore(
                dimension=settings.dimension,
                db_path=str(db_path),
                auto_load=False,
            )

            # 生成真实 embedding 并存储
            texts = [
                "Python 是一种流行的编程语言",
                "机器学习是人工智能的一个分支",
                "数据库是存储和管理数据的系统",
            ]

            points = []
            for i, text in enumerate(texts):
                vector = provider.embed(text)
                points.append(
                    VectorPoint(
                        id=f"test_{i}",
                        vector=vector,
                        payload={"text": text},
                    )
                )

            store.upsert(points)

            # 验证存储
            assert store.size() == 3

            # 创建新实例加载持久化数据
            store2 = FaissSqliteVectorStore(
                dimension=settings.dimension,
                db_path=str(db_path),
                auto_load=True,
            )

            assert store2.size() == 3

            # 使用真实 embedding 搜索
            query_vector = provider.embed("编程语言")
            hits = store2.search(query_vector, top_k=2)

            assert len(hits) >= 1
            # "Python 是一种流行的编程语言" 应该最相关
            assert hits[0].id == "test_0"


class TestE2EFlow:
    """端到端流程测试"""

    def test_complete_fusion_flow(
        self, ensure_service_running, test_workers
    ):
        """完整 Fusion 流程测试"""

        # 1. G1 基础融合
        g1_resp = requests.post(
            f"{BASE_URL}{FUSION_API_PREFIX}/groups/grp-e2e-g1/fuse",
            json={
                "question": "评估使用 Kubernetes 部署微服务的可行性",
                "participants": [test_workers[0]],
            },
            timeout=120,
        )
        assert g1_resp.status_code == 200
        g1_data = g1_resp.json()
        assert g1_data["fusion_mode"] == "agent"

        # 2. G2 冲突对齐
        g2_resp = requests.post(
            f"{BASE_URL}{FUSION_API_PREFIX}/groups/grp-e2e-g2/fuse",
            json={
                "question": "是否应该采用 NoSQL 数据库？",
                "participants": test_workers[:2],
                "fusion_mode": "conflict_alignment",
            },
            timeout=120,
        )
        assert g2_resp.status_code == 200
        g2_data = g2_resp.json()
        assert g2_data["fusion_mode"] == "conflict_alignment"

        # 3. G5 专家诊断
        g5_resp = requests.post(
            f"{BASE_URL}{FUSION_API_PREFIX}/groups/grp-e2e-g5/fuse",
            json={
                "question": "系统安全审计风险评估",
                "participants": [test_workers[2]],
                "fusion_mode": "expert_diagnosis",
            },
            timeout=120,
        )
        assert g5_resp.status_code == 200
        g5_data = g5_resp.json()
        assert g5_data["fusion_mode"] == "expert_diagnosis"

        # 验证所有响应都有有效的时间记录
        for data in [g1_data, g2_data, g5_data]:
            assert "timing" in data
            assert "duration_ms" in data["timing"]
            assert data["timing"]["duration_ms"] > 0


# =============================================================================
# 运行入口
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])