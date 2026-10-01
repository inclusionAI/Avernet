"""Tests for WorkerVectorMatchService.

WorkerVectorMatchService 职责：
- metadata filter
- vector ANN search
- lightweight rerank

不负责：
- participants sufficiency 检查
- recommendation 决策
- embedding 生成

==================================================
行为约定（必须在实现中严格遵守）：
==================================================

1. filters 语义：
   - 不同字段之间 = AND 语义
   - 同一字段的 list 值 = contains-any / OR 语义

   例如：
   - {"domains": ["backend", "frontend"]} 表示命中 backend 或 frontend 任一即可
   - {"domains": ["backend"], "active_skill_names": ["python"]} 表示同时满足 domain AND skill

2. excluded_profile_keys 语义：
   - 在最终结果里必须剔除
   - 不允许出现在返回结果中

3. vector store 失败语义：
   - 当前 baseline 选择"graceful degradation"
   - 即返回空列表 []
   - 不抛异常中断 recommendation 主流程

==================================================
三段式查询链路：
==================================================

1. metadata filter（通过 MetadataStore）
2. vector ANN search（通过 VectorStore）
3. lightweight rerank（根据配置加分）

==================================================
"""

import tempfile
from unittest.mock import Mock

import numpy as np
import pytest

from src.application.services.worker_vector_match_service import (
    WorkerVectorMatchService,
    MatchResult,
)
from src.domain.models.metadata_record import MetadataRecord
from src.domain.models.vector_point import VectorPoint
from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
from src.infra.vectorstores.faiss_vector_store_adapter import FaissVectorStoreAdapter


class FakeEmbeddingGenerator:
    """Generate deterministic fake embeddings for testing.

    不依赖真实 LLM，使用确定性伪随机生成。
    """

    def __init__(self, dimension: int = 64):
        self._dimension = dimension

    def generate(self, text: str) -> list[float]:
        """Generate a deterministic embedding from text."""
        np.random.seed(hash(text) % (2**32))
        vector = np.random.randn(self._dimension).astype(np.float32)
        # Normalize for cosine similarity
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        return vector.tolist()


class TestWorkerVectorMatchService:
    """Test WorkerVectorMatchService implementation."""

    # ========================================
    # Fixtures
    # ========================================

    @pytest.fixture
    def dimension(self):
        """Vector dimension for testing."""
        return 64

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def vector_store(self, dimension):
        """Create a FaissVectorStoreAdapter for testing."""
        return FaissVectorStoreAdapter(dimension=dimension)

    @pytest.fixture
    def metadata_store(self, temp_dir):
        """Create a FileMetadataStoreAdapter for testing."""
        return FileMetadataStoreAdapter(storage_dir=temp_dir)

    @pytest.fixture
    def embedding_gen(self, dimension):
        """Create fake embedding generator."""
        return FakeEmbeddingGenerator(dimension)

    @pytest.fixture
    def service(self, vector_store, metadata_store):
        """Create a WorkerVectorMatchService for testing."""
        return WorkerVectorMatchService(
            vector_store=vector_store,
            metadata_store=metadata_store,
        )

    @pytest.fixture
    def indexed_data(self, vector_store, metadata_store, embedding_gen):
        """Index sample data and return profile keys.

        创建4个测试 profile：
        - staff_001: backend, python/django
        - staff_002: frontend, javascript/react
        - staff_003: backend+frontend, python/javascript
        - staff_004: devops, kubernetes/docker
        """
        embeddings = [
            embedding_gen.generate("python backend developer"),
            embedding_gen.generate("javascript frontend developer"),
            embedding_gen.generate("python fullstack developer"),
            embedding_gen.generate("devops kubernetes engineer"),
        ]

        points = [
            VectorPoint(id="staff_001:default", vector=embeddings[0], payload={"staff_id": "001"}),
            VectorPoint(id="staff_002:default", vector=embeddings[1], payload={"staff_id": "002"}),
            VectorPoint(id="staff_003:default", vector=embeddings[2], payload={"staff_id": "003"}),
            VectorPoint(id="staff_004:default", vector=embeddings[3], payload={"staff_id": "004"}),
        ]

        records = [
            MetadataRecord(
                profile_key="staff_001:default",
                staff_id="001",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python", "django"],
                suitable_roles=["developer", "architect"],
                source_root="/data",
            ),
            MetadataRecord(
                profile_key="staff_002:default",
                staff_id="002",
                profile_id="default",
                profile_type="default",
                domains=["frontend"],
                active_skill_names=["javascript", "react"],
                suitable_roles=["developer"],
                source_root="/data",
            ),
            MetadataRecord(
                profile_key="staff_003:default",
                staff_id="003",
                profile_id="default",
                profile_type="default",
                domains=["backend", "frontend"],
                active_skill_names=["python", "javascript"],
                suitable_roles=["developer", "tech_lead"],
                source_root="/data",
            ),
            MetadataRecord(
                profile_key="staff_004:default",
                staff_id="004",
                profile_id="default",
                profile_type="default",
                domains=["devops"],
                active_skill_names=["kubernetes", "docker"],
                suitable_roles=["devops_engineer"],
                source_root="/data",
            ),
        ]

        vector_store.upsert(points)
        metadata_store.upsert(records)

        return ["staff_001:default", "staff_002:default", "staff_003:default", "staff_004:default"]

    # ========================================
    # Group 1: filters 语义测试
    # 约定：不同字段 AND，同字段 list OR
    # ========================================

    def test_filters_domains_or_semantics(self, service, indexed_data, embedding_gen):
        """Test: {"domains": ["backend", "frontend"]} = OR 语义。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=10,
            filters={"domains": ["backend", "frontend"]},
        )

        # 应该返回 staff_001(backend), staff_002(frontend), staff_003(both)
        assert len(results) >= 2
        for r in results:
            assert "backend" in r.metadata.domains or "frontend" in r.metadata.domains

    def test_filters_skills_or_semantics(self, service, indexed_data, embedding_gen):
        """Test: {"active_skill_names": ["python", "javascript"]} = OR 语义。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=10,
            filters={"active_skill_names": ["python", "javascript"]},
        )

        for r in results:
            assert "python" in r.metadata.active_skill_names or "javascript" in r.metadata.active_skill_names

    def test_filters_different_fields_and_semantics(self, service, indexed_data, embedding_gen):
        """Test: {"domains": ["backend"], "active_skill_names": ["python"]} = AND 语义。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=10,
            filters={
                "domains": ["backend"],
                "active_skill_names": ["python"],
            },
        )

        for r in results:
            assert "backend" in r.metadata.domains
            assert "python" in r.metadata.active_skill_names

    def test_filters_profile_type(self, service, indexed_data, embedding_gen):
        """Test: profile_type 过滤。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=10,
            filters={"profile_type": "default"},
        )

        for r in results:
            assert r.metadata.profile_type == "default"

    # ========================================
    # Group 2: excluded_profile_keys 语义测试
    # 约定：在最终结果里必须剔除
    # ========================================

    def test_excluded_profile_keys_removes_results(self, service, indexed_data, embedding_gen):
        """Test: excluded_profile_keys 必须在结果中剔除。"""
        query_embedding = embedding_gen.generate("python backend developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=10,
            excluded_profile_keys=["staff_001:default"],
        )

        profile_keys = [r.profile_key for r in results]
        assert "staff_001:default" not in profile_keys

    def test_excluded_profile_keys_multiple(self, service, indexed_data, embedding_gen):
        """Test: 可以排除多个 profile_keys。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=10,
            excluded_profile_keys=["staff_001:default", "staff_002:default"],
        )

        profile_keys = [r.profile_key for r in results]
        assert "staff_001:default" not in profile_keys
        assert "staff_002:default" not in profile_keys

    def test_excluded_profile_keys_with_filters(self, service, indexed_data, embedding_gen):
        """Test: excluded_profile_keys 与 filters 组合使用。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=10,
            filters={"domains": ["backend"]},
            excluded_profile_keys=["staff_001:default"],
        )

        profile_keys = [r.profile_key for r in results]
        assert "staff_001:default" not in profile_keys
        for r in results:
            assert "backend" in r.metadata.domains

    # ========================================
    # Group 3: vector store 失败语义测试
    # 约定：graceful degradation，返回空列表
    # ========================================

    def test_vector_store_error_returns_empty(self, metadata_store, embedding_gen):
        """Test: vector store 抛异常时返回空列表，不中断主流程。"""
        mock_vector_store = Mock()
        mock_vector_store.search.side_effect = RuntimeError("Vector store unavailable")
        mock_vector_store.size.return_value = 1

        service = WorkerVectorMatchService(
            vector_store=mock_vector_store,
            metadata_store=metadata_store,
        )

        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=10,
        )

        assert results == []

    def test_vector_store_empty_index_returns_empty(self, vector_store, metadata_store, embedding_gen):
        """Test: 空向量索引返回空列表。"""
        service = WorkerVectorMatchService(
            vector_store=vector_store,
            metadata_store=metadata_store,
        )

        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=10,
        )

        assert results == []

    # ========================================
    # Group 4: metadata filter 生效测试
    # ========================================

    def test_metadata_filter_reduces_candidates(self, service, indexed_data, embedding_gen):
        """Test: metadata filter 应该减少候选数量。"""
        query_embedding = embedding_gen.generate("developer")

        all_results = service.match(
            query_embedding=query_embedding,
            top_k=10,
        )

        filtered_results = service.match(
            query_embedding=query_embedding,
            top_k=10,
            filters={"domains": ["backend"]},
        )

        assert len(filtered_results) <= len(all_results)

    def test_metadata_filter_no_match_returns_empty(self, service, indexed_data, embedding_gen):
        """Test: 无匹配的 filter 返回空结果。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=10,
            filters={"domains": ["nonexistent_domain"]},
        )

        assert results == []

    # ========================================
    # Group 5: vector search 生效测试
    # ========================================

    def test_vector_search_returns_similar_results(self, service, indexed_data, embedding_gen):
        """Test: 向量搜索返回相似结果。"""
        query_embedding = embedding_gen.generate("python backend developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=3,
        )

        assert len(results) >= 1
        assert results[0].profile_key == "staff_001:default"

    def test_vector_search_ordering_by_similarity(self, service, indexed_data, embedding_gen):
        """Test: 结果按相似度降序排列。"""
        query_embedding = embedding_gen.generate("python backend developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=4,
        )

        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    # ========================================
    # Group 6: top_k 生效测试
    # ========================================

    def test_top_k_limits_results(self, service, indexed_data, embedding_gen):
        """Test: top_k 限制结果数量。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=2,
        )

        assert len(results) <= 2

    def test_top_k_larger_than_available(self, service, indexed_data, embedding_gen):
        """Test: top_k 大于可用数量时返回所有结果。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=100,
        )

        assert len(results) <= 4

    # ========================================
    # Group 8: 返回结果结构测试
    # ========================================

    def test_match_result_has_required_fields(self, service, indexed_data, embedding_gen):
        """Test: MatchResult 包含必需字段。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=1,
        )

        assert len(results) >= 1

        result = results[0]
        assert isinstance(result, MatchResult)
        assert hasattr(result, "profile_key")
        assert hasattr(result, "metadata")
        assert hasattr(result, "score")
        assert hasattr(result, "reasons")

    def test_match_result_field_types(self, service, indexed_data, embedding_gen):
        """Test: MatchResult 字段类型正确。"""
        query_embedding = embedding_gen.generate("developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=1,
        )

        result = results[0]
        assert isinstance(result.profile_key, str)
        assert isinstance(result.metadata, MetadataRecord)
        assert isinstance(result.score, float)
        assert isinstance(result.reasons, list)

    def test_match_result_reasons_populated(self, service, indexed_data, embedding_gen):
        """Test: MatchResult reasons 字段被填充。"""
        query_embedding = embedding_gen.generate("python backend developer")

        results = service.match(
            query_embedding=query_embedding,
            top_k=2,
            filters={"domains": ["backend"]},
        )

        assert len(results) >= 1
        result = results[0]
        assert len(result.reasons) >= 1

    # ========================================
    # Group 9: 三段式查询链路验证
    # ========================================

    def test_pipeline_metadata_filter_then_vector_search(self, service, indexed_data, embedding_gen):
        """Test: pipeline 先 filter 再 search。"""
        query_embedding = embedding_gen.generate("developer")

        all_results = service.match(
            query_embedding=query_embedding,
            top_k=10,
        )

        filtered_results = service.match(
            query_embedding=query_embedding,
            top_k=10,
            filters={"domains": ["backend"]},
        )

        assert len(filtered_results) <= len(all_results)

        for r in filtered_results:
            assert "backend" in r.metadata.domains

    # ========================================
    # Group 10: 空结果场景测试
    # ========================================

    def test_empty_index_returns_empty_result(self, vector_store, metadata_store, embedding_gen):
        """Test: 空索引返回空结果。"""
        service = WorkerVectorMatchService(
            vector_store=vector_store,
            metadata_store=metadata_store,
        )

        results = service.match(
            query_embedding=embedding_gen.generate("developer"),
            top_k=10,
        )

        assert results == []