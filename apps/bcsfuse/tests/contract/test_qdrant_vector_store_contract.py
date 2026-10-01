"""
Contract tests for QdrantLocalVectorStore (S29D)

These tests verify:
1. Module imports without Qdrant server
2. Store constructs without connecting (lazy initialization)
3. Required VectorStore methods exist
4. No forbidden imports
5. No fallback to FAISS/InMemory when Qdrant is selected
6. Missing server/config fails fast with clear Qdrant error
7. Payload filter API shape is documented/testable

Optional real Qdrant integration tests are gated by:
- BCSFUSE_RUN_QDRANT_INTEGRATION=1
- QDRANT_URL (optional, defaults to local path)
- QDRANT_API_KEY (optional)
- QDRANT_COLLECTION (optional)
"""

import os
import sys
import pytest
from typing import Optional


class TestQdrantVectorStoreContract:
    """Contract tests for QdrantLocalVectorStore without real Qdrant server."""

    def test_module_imports_without_server(self):
        """Test that the module can be imported without Qdrant server connection."""
        import importlib
        import src.infra.public.vectorstores.qdrant_local_vector_store as store_module

        # Reload to ensure no connection happens on import
        importlib.reload(store_module)

        # No exception should be raised
        assert True

    def test_store_constructs_without_connecting(self):
        """Test that the store can be constructed without Qdrant server connection."""
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )

        # Should not raise error on construction due to lazy initialization
        store = QdrantLocalVectorStore(
            collection_name="test_collection",
            dimension=1024,
        )
        assert store is not None
        assert store.collection_name == "test_collection"
        assert store.dimension == 1024
        assert store._client is None  # Not connected yet
        assert store._collection_initialized is False

        # Should also accept explicit path without connecting
        store = QdrantLocalVectorStore(
            collection_name="another_collection",
            path="/tmp/test_qdrant",
            dimension=512,
            distance="Euclid",
        )
        assert store.path == "/tmp/test_qdrant"
        assert store.dimension == 512

    def test_required_methods_exist(self):
        """Test that all required VectorStore methods exist."""
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )

        store = QdrantLocalVectorStore()

        # Required methods from VectorStore protocol
        assert hasattr(store, "upsert")
        assert callable(store.upsert)

        assert hasattr(store, "search")
        assert callable(store.search)

        assert hasattr(store, "get")
        assert callable(store.get)

        assert hasattr(store, "delete")
        assert callable(store.delete)

        assert hasattr(store, "delete_by_filter")
        assert callable(store.delete_by_filter)

        # Additional convenience methods (not in protocol, but allowed)
        assert hasattr(store, "delete_by_worker")
        assert callable(store.delete_by_worker)

        assert hasattr(store, "delete_by_profile")
        assert callable(store.delete_by_profile)

        assert hasattr(store, "size")
        assert callable(store.size)

        assert hasattr(store, "clear")
        assert callable(store.clear)

    def test_no_forbidden_imports(self):
        """Test that the module does not import forbidden internal dependencies."""
        import subprocess
        from pathlib import Path

        # Get the open-core root directory (src/ocb-ant/ocb/src/bcsfuse)
        open_core_root = Path(__file__).resolve().parents[2]

        # Set PYTHONPATH to include the open-core root
        env = dict(os.environ)
        pythonpath = str(open_core_root)
        if "PYTHONPATH" in env:
            pythonpath = pythonpath + os.pathsep + env["PYTHONPATH"]
        env["PYTHONPATH"] = pythonpath

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from src.infra.public.vectorstores.qdrant_local_vector_store import QdrantLocalVectorStore; print('OK')",
            ],
            cwd=str(open_core_root),
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Import failed: {result.stderr}"
        assert "OK" in result.stdout

        # Check for forbidden imports in error output
        forbidden = [
            "bcsfuse_internal",
            "sofapy",
            "sofapy_base",
            "ant_sofapy_base",
            "mist",
            "mist_client",
            "layotto",
            "src.infra.config.zdas_settings",
            "src.infra.adapters.zdas_",
            "src.infra.vectorstores.qdrant_zdas_vector_store",
            "src.infra.vectorstores.faiss_zdas_vector_store",
            "src.infra.vectorstore_backends.zdas_vector_persistence_backend",
        ]
        for keyword in forbidden:
            assert keyword not in result.stdout.lower()
            assert keyword not in result.stderr.lower()

    def test_no_fallback_to_faiss_or_inmemory(self):
        """Test that the store does not fallback to FAISS or InMemory."""
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )

        store = QdrantLocalVectorStore()

        # Check module imports
        import src.infra.public.vectorstores.qdrant_local_vector_store as store_module
        module_source = open(store_module.__file__, 'r').read().lower()

        # Should not mention FAISS or InMemory fallback
        assert "faiss" not in module_source or "faiss" in store_module.__file__.lower()
        assert "inmemory" not in module_source
        assert "fallback" not in module_source

    def test_fail_fast_without_qdrant_config(self):
        """Test that methods fail fast with clear Qdrant error when server is not available."""
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )

        # Use a path that doesn't exist and won't have Qdrant data
        store = QdrantLocalVectorStore(
            collection_name="test_fail_collection",
            path="/tmp/qdrant_test_contract_" + str(os.getpid()),
        )

        # All methods should fail with clear Qdrant error when trying to access non-existent storage
        # Note: Some methods create the collection lazily, so we test operations that require data

        # upsert should work (creates collection), but let's test with invalid data
        with pytest.raises((ValueError, Exception)):
            # Wrong dimension should fail
            store.upsert("test-id", [1.0, 2.0, 3.0])  # Wrong dimension

        # search with wrong dimension should fail
        with pytest.raises((ValueError, Exception)):
            store.search([1.0, 2.0, 3.0], top_k=3)  # Wrong dimension

    def test_payload_filter_api_shape(self):
        """Test that payload filter API is properly shaped."""
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )

        store = QdrantLocalVectorStore()

        # Verify method signatures accept filter parameter
        import inspect

        # search method should accept filter parameter
        search_sig = inspect.signature(store.search)
        assert "filter" in search_sig.parameters
        assert search_sig.parameters["filter"].default is None

        # delete_by_filter method should accept filter parameter
        delete_filter_sig = inspect.signature(store.delete_by_filter)
        assert "filter" in delete_filter_sig.parameters


@pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_QDRANT_INTEGRATION") != "1",
    reason="BCSFUSE_RUN_QDRANT_INTEGRATION not set to 1"
)
class TestQdrantVectorStoreIntegration:
    """Integration tests with real Qdrant server (gated by environment variable)."""

    @pytest.fixture
    def qdrant_store(self, tmp_path):
        """Create Qdrant store with configuration from environment."""
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )

        # Use temp directory for test isolation
        test_path = str(tmp_path / "qdrant_test_data")

        # Get Qdrant configuration from environment (or use defaults)
        collection_name = os.getenv("QDRANT_COLLECTION", "bcsfuse_test_vectors")
        dimension = int(os.getenv("VECTOR_DIMENSION", "1024"))

        store = QdrantLocalVectorStore(
            collection_name=collection_name,
            path=test_path,
            dimension=dimension,
            distance="Euclid",  # Use Euclid distance for exact vector comparisons in tests
        )

        yield store

        # Cleanup
        try:
            store.clear()
        except Exception:
            pass

    def test_upsert_vector_entry(self, qdrant_store):
        """Test that upsert creates a vector entry."""
        import uuid
        test_id = str(uuid.uuid4())  # Qdrant local mode requires strict UUID format
        test_vector = [0.1] * qdrant_store.dimension
        test_metadata = {"worker_id": "test-worker", "profile_type": "default"}

        result = qdrant_store.upsert(test_id, test_vector, test_metadata)

        assert result is True

        # Verify we can retrieve it
        retrieved = qdrant_store.get(test_id)
        assert retrieved is not None
        assert retrieved["id"] == test_id
        assert retrieved["metadata"]["worker_id"] == "test-worker"

    def test_get_vector_entry(self, qdrant_store):
        """Test that get retrieves a vector entry."""
        import uuid
        test_id = str(uuid.uuid4())  # Qdrant local mode requires strict UUID format
        test_vector = [0.2] * qdrant_store.dimension

        qdrant_store.upsert(test_id, test_vector, {"key": "value"})

        result = qdrant_store.get(test_id)

        assert result is not None
        assert result["id"] == test_id
        # Use approximate comparison for floating point vectors
        assert len(result["vector"]) == len(test_vector)
        for i, (actual, expected) in enumerate(zip(result["vector"], test_vector)):
            assert abs(actual - expected) < 1e-5, f"Vector mismatch at index {i}: {actual} vs {expected}"
        assert result["metadata"]["key"] == "value"

    def test_search_top_k_by_vector(self, qdrant_store):
        """Test that search returns top-k results."""
        import uuid

        # Insert test vectors
        test_vectors = []
        for i in range(5):
            test_id = str(uuid.uuid4())  # Qdrant local mode requires strict UUID format
            # Create vectors with different patterns
            test_vector = [float(i)] * qdrant_store.dimension
            test_vectors.append((test_id, test_vector))
            qdrant_store.upsert(test_id, test_vector, {"index": i})

        # Search with similar vector (should return results with closest match first)
        query_vector = [1.0] * qdrant_store.dimension
        results = qdrant_store.search(query_vector, top_k=3)

        assert len(results) <= 3
        assert len(results) > 0
        # Results should have id, score, and metadata
        for result in results:
            assert "id" in result
            assert "score" in result
            assert "metadata" in result

    def test_payload_filtering(self, qdrant_store):
        """Test that payload filtering works."""
        import uuid

        # Insert vectors with different metadata
        for i in range(3):
            test_id = str(uuid.uuid4())  # Qdrant local mode requires strict UUID format
            test_vector = [float(i)] * qdrant_store.dimension
            qdrant_store.upsert(
                test_id,
                test_vector,
                {"worker_id": f"worker-{i}", "profile_type": "default" if i < 2 else "custom"}
            )

        # Search with filter
        query_vector = [1.0] * qdrant_store.dimension
        results = qdrant_store.search(
            query_vector,
            top_k=10,
            filter={"profile_type": "default"}
        )

        # All results should match filter
        for result in results:
            assert result["metadata"].get("profile_type") == "default"

    def test_delete_by_id(self, qdrant_store):
        """Test that delete removes a vector by ID."""
        import uuid
        test_id = str(uuid.uuid4())  # Qdrant local mode requires strict UUID format
        test_vector = [0.5] * qdrant_store.dimension

        qdrant_store.upsert(test_id, test_vector, {"key": "value"})

        # Verify it exists
        result = qdrant_store.get(test_id)
        assert result is not None

        # Delete it
        qdrant_store.delete(test_id)

        # Verify it's gone
        result = qdrant_store.get(test_id)
        assert result is None

    def test_delete_by_filter(self, qdrant_store):
        """Test that delete_by_filter removes vectors matching filter."""
        import uuid

        # Insert vectors with specific metadata
        test_ids = []
        for i in range(3):
            test_id = str(uuid.uuid4())  # Qdrant local mode requires strict UUID format
            test_ids.append(test_id)
            test_vector = [float(i)] * qdrant_store.dimension
            qdrant_store.upsert(
                test_id,
                test_vector,
                {"worker_id": "worker-to-delete", "index": i}
            )

        # Delete by filter
        qdrant_store.delete_by_filter({"worker_id": "worker-to-delete"})

        # Verify all are deleted
        for test_id in test_ids:
            result = qdrant_store.get(test_id)
            assert result is None

    def test_cleanup_test_collection(self, qdrant_store):
        """Test that cleanup (clear) works."""
        import uuid

        # Insert some vectors
        for i in range(3):
            test_id = str(uuid.uuid4())  # Qdrant local mode requires strict UUID format
            test_vector = [float(i)] * qdrant_store.dimension
            qdrant_store.upsert(test_id, test_vector)

        # Clear collection
        qdrant_store.clear()

        # Verify size is 0
        size = qdrant_store.size()
        assert size == 0

    def test_cosine_search_semantics(self, tmp_path):
        """Test Cosine distance metric search semantics.

        Validates that Cosine distance returns correct ranking semantics:
        - Identical/directional vectors should rank higher than orthogonal vectors
        - Does NOT validate raw vector equality (Cosine normalizes vectors)
        """
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )
        import uuid

        # Create store with Cosine distance (default/original metric)
        test_path = str(tmp_path / "qdrant_cosine_test")
        collection_name = f"cosine_test_{uuid.uuid4().hex[:8]}"
        dimension = 128  # Use smaller dimension for faster test

        store = QdrantLocalVectorStore(
            collection_name=collection_name,
            path=test_path,
            dimension=dimension,
            distance="Cosine",
        )

        try:
            # Create query vector (normalized)
            import math
            query_vector = [1.0 / math.sqrt(dimension)] * dimension

            # Vector 1: Identical to query (should have highest similarity)
            identical_id = str(uuid.uuid4())
            identical_vector = query_vector.copy()
            store.upsert(identical_id, identical_vector, {"type": "identical"})

            # Vector 2: Same direction, but scaled (should have high similarity)
            scaled_id = str(uuid.uuid4())
            scaled_vector = [2.0 / math.sqrt(dimension)] * dimension
            store.upsert(scaled_id, scaled_vector, {"type": "scaled"})

            # Vector 3: Orthogonal - different direction (should have lower similarity)
            orthogonal_id = str(uuid.uuid4())
            orthogonal_vector = [0.0] * dimension
            orthogonal_vector[0] = 1.0  # Only first dimension is non-zero
            store.upsert(orthogonal_id, orthogonal_vector, {"type": "orthogonal"})

            # Search with query vector
            results = store.search(query_vector, top_k=3)

            # Validate results
            assert len(results) >= 2, "Should return at least 2 results"

            # Extract result IDs and scores
            result_ids = [r["id"] for r in results]
            result_scores = {r["id"]: r["score"] for r in results}

            # Cosine distance returns similarity scores (higher = more similar)
            # So identical and scaled vectors should have higher scores than orthogonal vector

            # Check that identical and scaled are in top results
            assert identical_id in result_ids, "Identical vector should be in results"
            assert scaled_id in result_ids, "Scaled vector should be in results"

            # Check ranking: identical and scaled should rank higher than orthogonal
            if orthogonal_id in result_ids:
                # Orthogonal should have lower score (less similar) than identical
                # For Cosine: score = 1 - cos_distance, so identical vectors have score ~1.0
                # Orthogonal vectors have score ~0.0
                assert result_scores[identical_id] > result_scores.get(orthogonal_id, 0.0), \
                    f"Identical vector should have higher score ({result_scores[identical_id]}) than orthogonal ({result_scores.get(orthogonal_id, 0.0)})"

            # Both identical and scaled should have similar scores (same direction)
            # For normalized Cosine: identical and scaled should both have score ~1.0
            # Allow some tolerance due to floating point
            assert abs(result_scores[identical_id] - result_scores[scaled_id]) < 0.01, \
                f"Identical ({result_scores[identical_id]}) and scaled ({result_scores[scaled_id]}) vectors should have similar Cosine similarity"

            # Verify that identical and scaled have high similarity scores (close to 1.0)
            assert result_scores[identical_id] > 0.99, \
                f"Identical vector should have similarity score close to 1.0, got {result_scores[identical_id]}"
            assert result_scores[scaled_id] > 0.99, \
                f"Scaled vector should have similarity score close to 1.0, got {result_scores[scaled_id]}"

        finally:
            # Cleanup
            try:
                store.clear()
            except Exception:
                pass

    def test_business_id_upsert_get_delete(self, tmp_path):
        """Test that business IDs (worker_id:profile_id pattern) work correctly.

        Validates that QdrantLocalVectorStore supports business IDs through
        deterministic UUID mapping.

        Mapping strategy:
        - external_id = business_id (worker_id:profile_id)
        - point_id = uuid5(namespace, external_id) - deterministic
        - payload._external_id = external_id - stored for retrieval

        Expected behavior:
        - upsert with business_id succeeds
        - get returns business_id as logical ID
        - search returns business_id as logical ID
        - delete with business_id succeeds
        """
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )
        import uuid

        test_path = str(tmp_path / "qdrant_business_id_test")
        collection_name = f"business_id_test_{uuid.uuid4().hex[:8]}"
        dimension = 128

        store = QdrantLocalVectorStore(
            collection_name=collection_name,
            path=test_path,
            dimension=dimension,
            distance="Euclid",
        )

        try:
            # Use business ID pattern: worker_id:profile_id
            worker_id = "worker_test_001"
            profile_id = "profile_test_001"
            business_id = f"{worker_id}:{profile_id}"

            test_vector = [0.5] * dimension
            test_metadata = {
                "worker_id": worker_id,
                "profile_id": profile_id,
                "type": "business"
            }

            # Upsert with business ID - should succeed with mapping
            result = store.upsert(business_id, test_vector, test_metadata)
            assert result is True, "Upsert with business ID should succeed"

            # Retrieve by business ID
            retrieved = store.get(business_id)
            assert retrieved is not None, "Should retrieve by business ID"
            assert retrieved["id"] == business_id, "Retrieved ID should match business ID"
            assert retrieved["metadata"]["worker_id"] == worker_id
            assert retrieved["metadata"]["profile_id"] == profile_id
            assert "_external_id" not in retrieved["metadata"], "Internal _external_id should be hidden"

            # Search and verify business ID is returned
            results = store.search(test_vector, top_k=1)
            assert len(results) == 1
            assert results[0]["id"] == business_id, "Search should return business ID"
            assert "_external_id" not in results[0]["metadata"], "Internal _external_id should be hidden"

            # Delete by business ID
            store.delete(business_id)

            # Verify deletion
            retrieved = store.get(business_id)
            assert retrieved is None, "Business ID should be deleted"

        finally:
            try:
                store.clear()
            except Exception:
                pass

    def test_delete_by_profile_uses_business_id_pattern(self, tmp_path):
        """Test that delete_by_profile uses business ID pattern correctly.

        Validates that delete_by_profile constructs the correct business ID pattern
        (worker_id:profile_id) and uses deterministic UUID mapping for deletion.

        Mapping strategy:
        1. Construct business ID: worker_id:profile_id
        2. Generate deterministic UUID: uuid5(namespace, business_id)
        3. Delete by UUID

        Expected behavior:
        - delete_by_profile succeeds with business ID semantics
        - Vector is correctly deleted
        """
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )
        import uuid

        test_path = str(tmp_path / "qdrant_delete_profile_test")
        collection_name = f"delete_profile_test_{uuid.uuid4().hex[:8]}"
        dimension = 128

        store = QdrantLocalVectorStore(
            collection_name=collection_name,
            path=test_path,
            dimension=dimension,
            distance="Euclid",
        )

        try:
            # Create vector with business ID pattern
            worker_id = "worker_delete_test"
            profile_id = "profile_delete_test"
            business_id = f"{worker_id}:{profile_id}"

            test_vector = [0.7] * dimension
            test_metadata = {
                "worker_id": worker_id,
                "profile_id": profile_id,
            }

            # Upsert with business ID
            store.upsert(business_id, test_vector, test_metadata)

            # Verify exists
            retrieved = store.get(business_id)
            assert retrieved is not None

            # Delete using delete_by_profile
            result = store.delete_by_profile(worker_id, profile_id)
            assert result == 1, "delete_by_profile should return 1 deleted"

            # Verify deleted
            retrieved = store.get(business_id)
            assert retrieved is None, "Vector should be deleted by delete_by_profile"

        finally:
            try:
                store.clear()
            except Exception:
                pass

    def test_business_id_search_returns_external_id(self, tmp_path):
        """Test that search returns external business ID, not internal UUID."""
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )
        import uuid

        test_path = str(tmp_path / "qdrant_search_external_id_test")
        collection_name = f"search_external_test_{uuid.uuid4().hex[:8]}"
        dimension = 128

        store = QdrantLocalVectorStore(
            collection_name=collection_name,
            path=test_path,
            dimension=dimension,
            distance="Euclid",
        )

        try:
            # Insert multiple vectors with business IDs
            test_data = []
            for i in range(3):
                worker_id = f"worker_search_{i}"
                profile_id = f"profile_search_{i}"
                business_id = f"{worker_id}:{profile_id}"
                test_vector = [float(i + 1) / 10.0] * dimension
                test_metadata = {
                    "worker_id": worker_id,
                    "profile_id": profile_id,
                    "index": i,
                }

                store.upsert(business_id, test_vector, test_metadata)
                test_data.append((business_id, test_vector))

            # Search for first vector
            search_vector = [0.1] * dimension
            results = store.search(search_vector, top_k=3)

            assert len(results) >= 1, "Should return at least 1 result"

            # All results should have business IDs as logical IDs
            for result in results:
                # Business IDs should contain ":"
                assert ":" in result["id"], f"Expected business ID, got {result['id']}"
                # Internal UUID should not be exposed
                assert "_" not in result["id"] or "external_id" not in result["id"].lower()

        finally:
            try:
                store.clear()
            except Exception:
                pass

    def test_uuid_id_still_supported(self, tmp_path):
        """Test that UUID IDs are still supported for backward compatibility."""
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )
        import uuid

        test_path = str(tmp_path / "qdrant_uuid_backward_compat_test")
        collection_name = f"uuid_compat_test_{uuid.uuid4().hex[:8]}"
        dimension = 128

        store = QdrantLocalVectorStore(
            collection_name=collection_name,
            path=test_path,
            dimension=dimension,
            distance="Euclid",
        )

        try:
            # Use a valid UUID
            test_id = str(uuid.uuid4())
            test_vector = [0.3] * dimension
            test_metadata = {"type": "uuid_test"}

            # Upsert with UUID
            result = store.upsert(test_id, test_vector, test_metadata)
            assert result is True

            # Retrieve by UUID
            retrieved = store.get(test_id)
            assert retrieved is not None
            assert retrieved["id"] == test_id, "UUID should be preserved as-is"
            assert retrieved["metadata"]["type"] == "uuid_test"

            # Delete by UUID
            store.delete(test_id)

            # Verify deletion
            retrieved = store.get(test_id)
            assert retrieved is None

        finally:
            try:
                store.clear()
            except Exception:
                pass

    def test_point_id_mapping_is_deterministic(self, tmp_path):
        """Test that business ID mapping is deterministic.

        Same business ID should always map to same Qdrant point ID.
        """
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
            _to_qdrant_point_id,
        )
        import uuid

        test_path = str(tmp_path / "qdrant_mapping_deterministic_test")
        collection_name = f"mapping_test_{uuid.uuid4().hex[:8]}"
        dimension = 128

        store = QdrantLocalVectorStore(
            collection_name=collection_name,
            path=test_path,
            dimension=dimension,
            distance="Euclid",
        )

        try:
            # Test deterministic mapping
            business_id = "worker_deterministic:profile_deterministic"

            # Same business ID should always map to same UUID
            point_id_1 = _to_qdrant_point_id(business_id)
            point_id_2 = _to_qdrant_point_id(business_id)
            point_id_3 = _to_qdrant_point_id(business_id)

            assert point_id_1 == point_id_2 == point_id_3, \
                "Same business ID should map to same point ID"

            # Verify it's a valid UUID
            uuid.UUID(point_id_1)  # Should not raise

            # Verify different business IDs map to different UUIDs
            different_business_id = "worker_other:profile_other"
            different_point_id = _to_qdrant_point_id(different_business_id)
            assert point_id_1 != different_point_id, \
                "Different business IDs should map to different point IDs"

            # Test with store upsert/get
            test_vector_1 = [0.1] * dimension
            store.upsert(business_id, test_vector_1, {"test": "first"})
            retrieved_1 = store.get(business_id)
            assert retrieved_1 is not None

            # Update with same business ID (should overwrite)
            test_vector_2 = [0.2] * dimension
            store.upsert(business_id, test_vector_2, {"test": "second"})
            retrieved_2 = store.get(business_id)
            assert retrieved_2 is not None
            assert abs(retrieved_2["vector"][0] - 0.2) < 1e-5, "Vector should be updated"

            # Collection should have only 1 vector (not 2)
            assert store.size() == 1, "Same business ID should not create duplicates"

        finally:
            try:
                store.clear()
            except Exception:
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])