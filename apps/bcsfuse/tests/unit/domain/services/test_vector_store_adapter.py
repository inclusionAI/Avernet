"""Tests for VectorStoreAdapter protocol."""

import pytest

from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit


class MockVectorStore:
    """Mock implementation of VectorStoreAdapter for testing."""

    def __init__(self):
        self._points: dict[str, VectorPoint] = {}

    def upsert(self, points: list[VectorPoint]) -> None:
        for point in points:
            self._points[point.id] = point

    def delete(self, ids: list[str]) -> None:
        for id in ids:
            self._points.pop(id, None)

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
    ) -> list[VectorSearchHit]:
        # Simplified mock implementation
        results = []
        for i, (id, point) in enumerate(self._points.items()):
            if i >= top_k:
                break
            results.append(VectorSearchHit(id=id, score=0.9 - i * 0.1))
        return results

    def batch_search(
        self,
        vectors: list[list[float]],
        top_k: int,
        filters: dict | None = None,
    ) -> list[list[VectorSearchHit]]:
        return [self.search(v, top_k, filters) for v in vectors]

    def save_snapshot(self, path: str) -> None:
        pass

    def load_snapshot(self, path: str) -> None:
        pass

    def size(self) -> int:
        return len(self._points)


class TestVectorStoreAdapterProtocol:
    """Test VectorStoreAdapter protocol."""

    def test_protocol_is_runtime_checkable(self):
        """Test that VectorStoreAdapter is runtime checkable."""
        mock_store = MockVectorStore()

        # Should pass isinstance check
        assert isinstance(mock_store, VectorStoreAdapter)

    def test_protocol_methods_exist(self):
        """Test that all required methods exist in implementation."""
        mock_store = MockVectorStore()

        # Check all methods exist
        assert hasattr(mock_store, "upsert")
        assert hasattr(mock_store, "delete")
        assert hasattr(mock_store, "search")
        assert hasattr(mock_store, "batch_search")
        assert hasattr(mock_store, "save_snapshot")
        assert hasattr(mock_store, "load_snapshot")
        assert hasattr(mock_store, "size")

    def test_protocol_upsert_method(self):
        """Test upsert method signature."""
        mock_store = MockVectorStore()

        points = [
            VectorPoint(id="test_1", vector=[0.1, 0.2, 0.3]),
            VectorPoint(id="test_2", vector=[0.4, 0.5, 0.6]),
        ]

        # Should not raise
        mock_store.upsert(points)

        assert mock_store.size() == 2

    def test_protocol_delete_method(self):
        """Test delete method signature."""
        mock_store = MockVectorStore()

        points = [VectorPoint(id="test_1", vector=[0.1, 0.2, 0.3])]
        mock_store.upsert(points)

        # Should not raise
        mock_store.delete(["test_1"])

        assert mock_store.size() == 0

    def test_protocol_search_method(self):
        """Test search method signature."""
        mock_store = MockVectorStore()

        points = [
            VectorPoint(id="test_1", vector=[0.1, 0.2, 0.3]),
            VectorPoint(id="test_2", vector=[0.4, 0.5, 0.6]),
        ]
        mock_store.upsert(points)

        results = mock_store.search(vector=[0.2, 0.3, 0.4], top_k=2)

        assert isinstance(results, list)
        assert len(results) <= 2
        if len(results) > 0:
            assert isinstance(results[0], VectorSearchHit)

    def test_protocol_batch_search_method(self):
        """Test batch_search method signature."""
        mock_store = MockVectorStore()

        points = [
            VectorPoint(id="test_1", vector=[0.1, 0.2, 0.3]),
            VectorPoint(id="test_2", vector=[0.4, 0.5, 0.6]),
        ]
        mock_store.upsert(points)

        vectors = [
            [0.1, 0.2, 0.3],
            [0.4, 0.5, 0.6],
        ]
        results = mock_store.batch_search(vectors=vectors, top_k=2)

        assert isinstance(results, list)
        assert len(results) == 2
        assert isinstance(results[0], list)

    def test_protocol_size_method(self):
        """Test size method signature."""
        mock_store = MockVectorStore()

        assert mock_store.size() == 0

        points = [VectorPoint(id="test_1", vector=[0.1, 0.2, 0.3])]
        mock_store.upsert(points)

        assert mock_store.size() == 1

    def test_protocol_save_load_snapshot_methods(self):
        """Test save/load_snapshot method signatures."""
        mock_store = MockVectorStore()

        # Should not raise
        mock_store.save_snapshot("/tmp/test_index.faiss")
        mock_store.load_snapshot("/tmp/test_index.faiss")

    def test_protocol_with_filters_parameter(self):
        """Test that search methods accept filters parameter."""
        mock_store = MockVectorStore()

        points = [VectorPoint(id="test_1", vector=[0.1, 0.2, 0.3])]
        mock_store.upsert(points)

        # Should accept filters parameter
        results = mock_store.search(
            vector=[0.1, 0.2, 0.3],
            top_k=2,
            filters={"domain": "backend"}
        )

        assert isinstance(results, list)