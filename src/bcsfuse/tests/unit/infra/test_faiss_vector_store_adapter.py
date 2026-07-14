"""Tests for FaissVectorStoreAdapter implementation."""

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit
from src.infra.vectorstores.faiss_vector_store_adapter import FaissVectorStoreAdapter


class TestFaissVectorStoreAdapter:
    """Test FaissVectorStoreAdapter implementation."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def store(self):
        """Create a fresh FaissVectorStoreAdapter for each test."""
        return FaissVectorStoreAdapter(dimension=4)

    @pytest.fixture
    def sample_vector(self):
        """Create a sample 4-dimensional vector."""
        return [0.1, 0.2, 0.3, 0.4]

    @pytest.fixture
    def sample_vectors(self):
        """Create multiple sample vectors."""
        return [
            [0.1, 0.2, 0.3, 0.4],
            [0.5, 0.6, 0.7, 0.8],
            [0.2, 0.3, 0.4, 0.5],
        ]

    @pytest.fixture
    def sample_points(self, sample_vectors):
        """Create sample VectorPoints for testing."""
        return [
            VectorPoint(id=f"vector_{i}", vector=v)
            for i, v in enumerate(sample_vectors)
        ]

    # ========================================
    # Basic Operations Tests
    # ========================================

    def test_create_adapter_with_dimension(self):
        """Test creating adapter with specified dimension."""
        store = FaissVectorStoreAdapter(dimension=128)
        assert store.dimension == 128
        assert store.size() == 0

    def test_upsert_single_point(self, store, sample_vector):
        """Test upserting a single vector point."""
        point = VectorPoint(id="test_1", vector=sample_vector)
        store.upsert([point])

        assert store.size() == 1

    def test_upsert_multiple_points(self, store, sample_points):
        """Test upserting multiple vector points."""
        store.upsert(sample_points)

        assert store.size() == 3

    def test_upsert_updates_existing_id(self, store, sample_vector):
        """Test that upsert updates existing point with same id."""
        point1 = VectorPoint(id="test_1", vector=sample_vector)
        store.upsert([point1])

        # Update with different vector
        new_vector = [0.9, 0.8, 0.7, 0.6]
        point2 = VectorPoint(id="test_1", vector=new_vector)
        store.upsert([point2])

        assert store.size() == 1

        # Verify the vector was updated
        results = store.search(new_vector, top_k=1)
        assert len(results) == 1
        assert results[0].id == "test_1"
        assert results[0].score >= 0.99  # Should be very similar to itself

    def test_upsert_with_payload(self, store, sample_vector):
        """Test upserting vectors with payload."""
        point = VectorPoint(
            id="test_1",
            vector=sample_vector,
            payload={"staff_id": "123", "profile_type": "default"}
        )
        store.upsert([point])

        results = store.search(sample_vector, top_k=1)
        assert len(results) == 1
        assert results[0].payload == {"staff_id": "123", "profile_type": "default"}

    def test_upsert_wrong_dimension_raises_error(self, store):
        """Test that upserting vector with wrong dimension raises error."""
        wrong_vector = [0.1, 0.2, 0.3]  # 3D instead of 4D
        point = VectorPoint(id="test_1", vector=wrong_vector)

        with pytest.raises(ValueError, match="dimension"):
            store.upsert([point])

    def test_upsert_empty_list(self, store):
        """Test upserting empty list is safe."""
        store.upsert([])
        assert store.size() == 0

    def test_delete_single_point(self, store, sample_points):
        """Test deleting a single point."""
        store.upsert(sample_points)
        assert store.size() == 3

        store.delete(["vector_0"])

        assert store.size() == 2
        # Verify the correct one was deleted
        assert "vector_0" not in store.get_vector_ids()

    def test_delete_multiple_points(self, store, sample_points):
        """Test deleting multiple points."""
        store.upsert(sample_points)
        assert store.size() == 3

        store.delete(["vector_0", "vector_1"])

        assert store.size() == 1
        remaining_ids = store.get_vector_ids()
        assert "vector_2" in remaining_ids

    def test_delete_nonexistent_id(self, store, sample_points):
        """Test deleting nonexistent id is safe."""
        store.upsert(sample_points)
        assert store.size() == 3

        # Should not raise
        store.delete(["nonexistent"])

        assert store.size() == 3

    def test_delete_empty_list(self, store, sample_points):
        """Test deleting empty list is safe."""
        store.upsert(sample_points)
        store.delete([])
        assert store.size() == 3

    # ========================================
    # Search Tests
    # ========================================

    def test_search_single_result(self, store, sample_points):
        """Test searching returns correct result."""
        store.upsert(sample_points)

        # Search with the first vector
        query = sample_points[0].vector
        results = store.search(query, top_k=1)

        assert len(results) == 1
        assert results[0].id == "vector_0"
        assert isinstance(results[0], VectorSearchHit)
        assert isinstance(results[0].score, float)

    def test_search_multiple_results(self, store, sample_points):
        """Test searching returns multiple results in order."""
        store.upsert(sample_points)

        # Search with [0.2, 0.3, 0.4, 0.5] - should be closest to vector_2
        query = [0.2, 0.3, 0.4, 0.5]
        results = store.search(query, top_k=3)

        assert len(results) == 3
        # Results should be sorted by score (descending)
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_search_with_top_k_larger_than_size(self, store, sample_points):
        """Test search with top_k larger than available vectors."""
        store.upsert(sample_points[:2])

        results = store.search(sample_points[0].vector, top_k=10)

        # Should only return available vectors
        assert len(results) == 2

    def test_search_exact_match(self, store, sample_vector):
        """Test searching exact match returns high score."""
        point = VectorPoint(id="test_1", vector=sample_vector)
        store.upsert([point])

        results = store.search(sample_vector, top_k=1)

        assert len(results) == 1
        assert results[0].id == "test_1"
        # For normalized vectors with inner product, exact match should have score ~1.0
        assert results[0].score > 0.99

    def test_search_empty_index_raises_error(self, store, sample_vector):
        """Test searching empty index raises error."""
        with pytest.raises(ValueError, match="empty"):
            store.search(sample_vector, top_k=1)

    def test_search_wrong_dimension_raises_error(self, store, sample_points):
        """Test searching with wrong dimension raises error."""
        store.upsert(sample_points)

        wrong_query = [0.1, 0.2, 0.3]  # 3D instead of 4D

        with pytest.raises(ValueError, match="dimension"):
            store.search(wrong_query, top_k=1)

    def test_search_with_filters_parameter(self, store, sample_points):
        """Test that search accepts filters parameter (may be ignored)."""
        store.upsert(sample_points)

        # Filters parameter should be accepted even if not used
        results = store.search(
            sample_points[0].vector,
            top_k=1,
            filters={"domain": "backend"}
        )

        assert len(results) == 1

    # ========================================
    # Batch Search Tests
    # ========================================

    def test_batch_search_single_query(self, store, sample_points):
        """Test batch search with single query."""
        store.upsert(sample_points)

        queries = [sample_points[0].vector]
        results = store.batch_search(queries, top_k=2)

        assert len(results) == 1
        assert len(results[0]) == 2

    def test_batch_search_multiple_queries(self, store, sample_points):
        """Test batch search with multiple queries."""
        store.upsert(sample_points)

        queries = [
            sample_points[0].vector,
            sample_points[1].vector,
        ]
        results = store.batch_search(queries, top_k=2)

        assert len(results) == 2
        assert len(results[0]) == 2
        assert len(results[1]) == 2

    def test_batch_search_empty_index_raises_error(self, store, sample_vector):
        """Test batch searching empty index raises error."""
        with pytest.raises(ValueError, match="empty"):
            store.batch_search([sample_vector], top_k=1)

    def test_batch_search_with_filters_parameter(self, store, sample_points):
        """Test that batch_search accepts filters parameter."""
        store.upsert(sample_points)

        queries = [sample_points[0].vector]
        results = store.batch_search(
            queries,
            top_k=1,
            filters={"domain": "backend"}
        )

        assert len(results) == 1

    # ========================================
    # Save/Load Tests
    # ========================================

    def test_save_creates_files(self, store, sample_points, temp_dir):
        """Test that save creates the expected files."""
        store.upsert(sample_points)
        store.save_snapshot(temp_dir)

        # Check index.faiss exists
        index_path = Path(temp_dir) / "index.faiss"
        assert index_path.exists()

        # Check id_map.json exists
        id_map_path = Path(temp_dir) / "id_map.json"
        assert id_map_path.exists()

        # Check payload_map.json exists
        payload_map_path = Path(temp_dir) / "payload_map.json"
        assert payload_map_path.exists()

    def test_save_and_load_preserves_data(self, temp_dir, sample_points):
        """Test that save and load preserves all data."""
        # Create store and add data
        dimension = 4
        store1 = FaissVectorStoreAdapter(dimension=dimension)
        store1.upsert(sample_points)
        store1.save_snapshot(temp_dir)

        # Create new store and load data
        store2 = FaissVectorStoreAdapter(dimension=dimension)
        store2.load_snapshot(temp_dir)

        assert store2.size() == 3
        assert store2.dimension == dimension

        # Verify vectors can be searched
        results = store2.search(sample_points[0].vector, top_k=1)
        assert len(results) == 1
        assert results[0].id == "vector_0"

    def test_save_and_load_preserves_payloads(self, temp_dir, sample_vector):
        """Test that save and load preserves payloads."""
        dimension = 4
        store1 = FaissVectorStoreAdapter(dimension=dimension)

        points = [
            VectorPoint(
                id="test_1",
                vector=sample_vector,
                payload={"staff_id": "123", "key": "value"}
            )
        ]
        store1.upsert(points)
        store1.save_snapshot(temp_dir)

        store2 = FaissVectorStoreAdapter(dimension=dimension)
        store2.load_snapshot(temp_dir)

        results = store2.search(sample_vector, top_k=1)
        assert results[0].payload == {"staff_id": "123", "key": "value"}

    def test_load_creates_index_if_not_exists(self, temp_dir, sample_points):
        """Test that calling load on new adapter loads the index."""
        # Save data
        store1 = FaissVectorStoreAdapter(dimension=4)
        store1.upsert(sample_points)
        store1.save_snapshot(temp_dir)

        # Load into fresh adapter
        store2 = FaissVectorStoreAdapter(dimension=4)
        store2.load_snapshot(temp_dir)

        assert store2.size() == 3

    def test_load_nonexistent_file_raises_error(self, store):
        """Test loading nonexistent file raises error."""
        with pytest.raises(FileNotFoundError):
            store.load_snapshot("/nonexistent/path")

    def test_save_empty_index(self, store, temp_dir):
        """Test saving empty index."""
        store.save_snapshot(temp_dir)

        # Files should be created
        index_path = Path(temp_dir) / "index.faiss"
        assert index_path.exists()

    def test_load_dimension_mismatch_raises_error(self, temp_dir, sample_points):
        """Test loading with wrong dimension raises error."""
        # Save with dimension 4
        store1 = FaissVectorStoreAdapter(dimension=4)
        store1.upsert(sample_points)
        store1.save_snapshot(temp_dir)

        # Try to load with dimension 8
        store2 = FaissVectorStoreAdapter(dimension=8)

        with pytest.raises(ValueError, match="dimension"):
            store2.load_snapshot(temp_dir)

    # ========================================
    # Vector ID Mapping Tests
    # ========================================

    def test_get_vector_ids(self, store, sample_points):
        """Test getting all vector IDs."""
        store.upsert(sample_points)

        ids = store.get_vector_ids()

        assert len(ids) == 3
        assert "vector_0" in ids
        assert "vector_1" in ids
        assert "vector_2" in ids

    def test_get_vector_ids_after_delete(self, store, sample_points):
        """Test getting vector IDs after deletion."""
        store.upsert(sample_points)
        store.delete(["vector_1"])

        ids = store.get_vector_ids()

        assert len(ids) == 2
        assert "vector_0" in ids
        assert "vector_2" in ids
        assert "vector_1" not in ids

    def test_size_consistency(self, store, sample_points):
        """Test that size is consistent with actual count."""
        assert store.size() == 0

        store.upsert(sample_points[:2])
        assert store.size() == 2
        assert len(store.get_vector_ids()) == 2

        store.upsert([sample_points[2]])
        assert store.size() == 3
        assert len(store.get_vector_ids()) == 3

        store.delete(["vector_0"])
        assert store.size() == 2
        assert len(store.get_vector_ids()) == 2

    # ========================================
    # Edge Cases
    # ========================================

    def test_large_number_of_vectors(self, store):
        """Test handling a large number of vectors."""
        dimension = 4
        num_vectors = 100

        points = [
            VectorPoint(
                id=f"vec_{i}",
                vector=[0.01 * i, 0.02 * i, 0.03 * i, 0.04 * i]
            )
            for i in range(num_vectors)
        ]

        store.upsert(points)

        assert store.size() == num_vectors

        # Search should work
        results = store.search([0.5, 0.6, 0.7, 0.8], top_k=10)
        assert len(results) == 10

    def test_very_similar_vectors(self, store):
        """Test handling very similar vectors."""
        points = [
            VectorPoint(id=f"vec_{i}", vector=[0.1 + 0.001 * i, 0.2, 0.3, 0.4])
            for i in range(10)
        ]

        store.upsert(points)

        # Search with the middle vector
        results = store.search([0.105, 0.2, 0.3, 0.4], top_k=5)

        assert len(results) == 5
        # All should have high scores
        for hit in results:
            assert hit.score > 0.9

    def test_zero_vector(self, store):
        """Test handling zero vectors."""
        point = VectorPoint(id="zero", vector=[0.0, 0.0, 0.0, 0.0])
        store.upsert([point])

        query = [0.0, 0.0, 0.0, 0.0]
        results = store.search(query, top_k=1)

        assert len(results) == 1

    def test_negative_vector_values(self, store):
        """Test handling negative vector values."""
        points = [
            VectorPoint(id="pos", vector=[0.5, 0.5, 0.5, 0.5]),
            VectorPoint(id="neg", vector=[-0.5, -0.5, -0.5, -0.5]),
            VectorPoint(id="mixed", vector=[0.5, -0.5, 0.5, -0.5]),
        ]

        store.upsert(points)

        # Search with positive query
        results = store.search([0.5, 0.5, 0.5, 0.5], top_k=3)

        assert len(results) == 3

    def test_upsert_after_delete(self, store, sample_points):
        """Test upserting after deletion."""
        store.upsert(sample_points)
        store.delete(["vector_0"])

        # Upsert new point with same id
        new_point = VectorPoint(id="vector_0", vector=[0.9, 0.9, 0.9, 0.9])
        store.upsert([new_point])

        assert store.size() == 3
        results = store.search([0.9, 0.9, 0.9, 0.9], top_k=1)
        assert results[0].id == "vector_0"

    # ========================================
    # Protocol Compliance Test
    # ========================================

    def test_satisfies_protocol(self, store):
        """Test that FaissVectorStoreAdapter satisfies VectorStoreAdapter protocol."""
        from src.domain.services.vector_store_adapter import VectorStoreAdapter

        assert isinstance(store, VectorStoreAdapter)

    # ========================================
    # Normalization Tests (important for Inner Product)
    # ========================================

    def test_vectors_normalized_on_upsert(self, store):
        """Test that vectors are normalized on upsert for inner product."""
        # Non-normalized vector
        vector = [3.0, 4.0, 0.0, 0.0]  # Norm = 5
        point = VectorPoint(id="test", vector=vector)

        store.upsert([point])

        # Search with same non-normalized query
        results = store.search(vector, top_k=1)

        # Should still work correctly because vectors are normalized
        assert len(results) == 1
        assert results[0].id == "test"
        assert results[0].score > 0.99  # Should match perfectly

    def test_query_normalization(self, store):
        """Test that queries are normalized before search."""
        vector = [1.0, 0.0, 0.0, 0.0]
        point = VectorPoint(id="test", vector=vector)
        store.upsert([point])

        # Search with scaled query (same direction)
        scaled_query = [5.0, 0.0, 0.0, 0.0]

        results = store.search(scaled_query, top_k=1)

        assert len(results) == 1
        assert results[0].id == "test"
        assert results[0].score > 0.99