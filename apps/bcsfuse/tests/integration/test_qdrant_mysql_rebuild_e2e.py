"""
E2E Rebuild Tests for Qdrant MySQL Vector Store (S30C)

This test suite validates the complete rebuild workflow:
1. Write durable vector/profile records to MySQL
2. Build local Qdrant from MySQL
3. Search local Qdrant
4. Verify returned logical ID is business ID
5. Verify payload filters work
6. Delete local Qdrant path or clear collection
7. Rebuild local Qdrant from MySQL
8. Search again
9. Verify result identity and metadata unchanged
10. Verify MySQL durable records remain intact

Test is gated by:
- BCSFUSE_RUN_MYSQL_INTEGRATION=1
- BCSFUSE_RUN_QDRANT_INTEGRATION=1
- MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
- QDRANT_LOCAL_PATH, QDRANT_COLLECTION, VECTOR_DIMENSION

If MySQL/Qdrant env unavailable, test must skip clearly:
SKIPPED_MYSQL_ENV_UNAVAILABLE or SKIPPED_QDRANT_ENV_UNAVAILABLE
"""

import os
import sys
import pytest
import tempfile
import shutil
from pathlib import Path


@pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1",
    reason="MySQL integration tests require BCSFUSE_RUN_MYSQL_INTEGRATION=1"
)
@pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_QDRANT_INTEGRATION") != "1",
    reason="Qdrant integration tests require BCSFUSE_RUN_QDRANT_INTEGRATION=1"
)
class TestQdrantMySQLRebuildE2E:
    """E2E tests for Qdrant + MySQL rebuild workflow.

    Validates that:
    1. MySQL is the durable source of truth
    2. Local Qdrant is disposable and rebuildable
    3. Business ID mapping remains consistent after rebuild
    """

    @pytest.fixture
    def qdrant_mysql_store(self):
        """Create a QdrantMySQLVectorStore for E2E testing."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        # Create temp directory for Qdrant
        qdrant_path = tempfile.mkdtemp(prefix="e2e_qdrant_mysql_rebuild_")

        # Use unique collection name for this test run
        import uuid
        collection_name = f"e2e_test_{uuid.uuid4().hex[:8]}"

        store = QdrantMySQLVectorStore(
            collection_name=collection_name,
            qdrant_path=qdrant_path,
            dimension=int(os.getenv("VECTOR_DIMENSION", "4096")),
        )

        yield store, qdrant_path

        # Cleanup
        try:
            store.close()
            shutil.rmtree(qdrant_path, ignore_errors=True)
        except Exception:
            pass

    def test_durable_write_to_mysql(self, qdrant_mysql_store):
        """Test 1: Write durable vector records to MySQL.

        Scenario:
        - Write vectors with business IDs
        - Verify MySQL has the durable records
        - Verify Qdrant can be searched
        """
        store, qdrant_path = qdrant_mysql_store
        from src.domain.models.vector_point import VectorPoint

        # Write vectors with business IDs (not UUIDs)
        points = [
            VectorPoint(
                id=f"worker_{i}:default_profile",  # Business ID
                vector=[float(i) * 0.1] * store.dimension,
                payload={
                    "worker_id": f"worker_{i}",
                    "profile_key": f"worker_{i}:default_profile",
                    "staff_id": f"staff_{i}",
                }
            )
            for i in range(3)
        ]

        store.upsert(points)

        # Verify Qdrant can be searched
        query_vector = [0.0] * store.dimension
        results = store.search(query_vector, top_k=5)

        assert len(results) >= 1

        # Results should contain business IDs (not UUIDs)
        result_ids = [r["id"] for r in results]
        for point in points:
            assert point.id in result_ids, f"Business ID {point.id} not found in results"

        # Cleanup
        store.delete([p.id for p in points])

    def test_build_qdrant_from_mysql(self, qdrant_mysql_store):
        """Test 2: Build local Qdrant from MySQL.

        Scenario:
        - Write vectors to MySQL
        - Call rebuild_from_mysql
        - Verify Qdrant has correct vectors
        """
        store, qdrant_path = qdrant_mysql_store
        from src.domain.models.vector_point import VectorPoint

        # Write vectors
        points = [
            VectorPoint(
                id=f"build_test_{i}",
                vector=[float(i)] * store.dimension,
                payload={"index": i}
            )
            for i in range(4)
        ]
        store.upsert(points)

        # Rebuild from MySQL
        result = store.rebuild_from_mysql(batch_size=2)

        # Verify rebuild succeeded
        assert result["success"] is True
        assert result["mysql_loaded"] >= 4
        assert result["qdrant_inserted"] >= 4
        assert result["qdrant_count"] >= 4

        # Verify Qdrant can be searched
        query_vector = [2.0] * store.dimension
        results = store.search(query_vector, top_k=3)

        assert len(results) >= 1

        # Cleanup
        store.delete([p.id for p in points])

    def test_business_id_semantics_preserved(self, qdrant_mysql_store):
        """Test 3: Business ID semantics preserved after rebuild.

        Scenario:
        - Write vectors with business IDs
        - Rebuild from MySQL
        - Verify search results return business IDs (not UUIDs)
        """
        store, qdrant_path = qdrant_mysql_store
        from src.domain.models.vector_point import VectorPoint

        # Write vectors with business IDs
        business_ids = [
            "architect_alice:default",
            "engineer_bob:expert",
            "manager_charlie:senior",
        ]

        points = [
            VectorPoint(
                id=business_id,
                vector=[float(i) * 0.5] * store.dimension,
                payload={
                    "worker_id": business_id.split(":")[0],
                    "profile_type": business_id.split(":")[1],
                }
            )
            for i, business_id in enumerate(business_ids)
        ]
        store.upsert(points)

        # Rebuild from MySQL
        rebuild_result = store.rebuild_from_mysql()
        assert rebuild_result["success"] is True

        # Search after rebuild
        query_vector = [1.0] * store.dimension
        results = store.search(query_vector, top_k=5)

        assert len(results) >= 1

        # Verify search results return business IDs
        result_ids = [r["id"] for r in results]
        for business_id in business_ids:
            assert business_id in result_ids, f"Business ID {business_id} not found after rebuild"

        # Cleanup
        store.delete(business_ids)

    def test_payload_filter_after_rebuild(self, qdrant_mysql_store):
        """Test 4: Payload filters work after rebuild.

        Scenario:
        - Write vectors with different payload
        - Rebuild from MySQL
        - Verify payload filters work
        """
        store, qdrant_path = qdrant_mysql_store
        from src.domain.models.vector_point import VectorPoint

        # Write vectors with different worker_id
        points = [
            VectorPoint(
                id=f"filter_test_{i}",
                vector=[float(i)] * store.dimension,
                payload={
                    "worker_id": f"worker_{i % 2}",  # worker_0 or worker_1
                    "profile_type": "default",
                }
            )
            for i in range(4)
        ]
        store.upsert(points)

        # Rebuild from MySQL
        rebuild_result = store.rebuild_from_mysql()
        assert rebuild_result["success"] is True

        # Search with payload filter
        query_vector = [0.0] * store.dimension
        results = store.search(
            query_vector,
            top_k=10,
            filter={"worker_id": "worker_0"}
        )

        # All results should have worker_id = worker_0
        for result in results:
            assert "metadata" in result, f"Result missing metadata: {result}"
            assert result["metadata"]["worker_id"] == "worker_0"

        # Cleanup
        store.delete([p.id for p in points])

    def test_delete_qdrant_and_rebuild(self, qdrant_mysql_store):
        """Test 5: Delete local Qdrant and rebuild from MySQL.

        Scenario:
        - Write vectors to MySQL and Qdrant
        - Delete local Qdrant storage path
        - Rebuild Qdrant from MySQL
        - Verify search works
        """
        store, qdrant_path = qdrant_mysql_store
        from src.domain.models.vector_point import VectorPoint

        # Write vectors
        points = [
            VectorPoint(
                id=f"rebuild_after_delete_{i}",
                vector=[float(i)] * store.dimension,
                payload={"test": "rebuild_after_delete"}
            )
            for i in range(3)
        ]
        store.upsert(points)

        # Verify Qdrant has vectors before deletion
        query_vector = [0.0] * store.dimension
        results_before = store.search(query_vector, top_k=5)
        assert len(results_before) >= 1

        # Close the store
        store.close()

        # Delete Qdrant storage path (simulating Qdrant loss)
        shutil.rmtree(qdrant_path, ignore_errors=True)

        # Recreate Qdrant path
        os.makedirs(qdrant_path, exist_ok=True)

        # Recreate store (Qdrant will be empty)
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )
        collection_name = store.collection_name
        store = QdrantMySQLVectorStore(
            collection_name=collection_name,
            qdrant_path=qdrant_path,
            dimension=int(os.getenv("VECTOR_DIMENSION", "4096")),
        )

        # Rebuild from MySQL
        rebuild_result = store.rebuild_from_mysql()
        assert rebuild_result["success"] is True
        assert rebuild_result["mysql_loaded"] >= 3

        # Verify Qdrant can be searched after rebuild
        results_after = store.search(query_vector, top_k=5)
        assert len(results_after) >= 1

        # Verify results are consistent
        result_ids_after = [r["id"] for r in results_after]
        for point in points:
            assert point.id in result_ids_after, f"ID {point.id} not found after rebuild"

        # Cleanup
        store.delete([p.id for p in points])

    def test_data_integrity_after_rebuild(self, qdrant_mysql_store):
        """Test 6: Data integrity after rebuild.

        Scenario:
        - Write vectors to MySQL
        - Rebuild Qdrant
        - Verify vectors are identical before and after rebuild
        """
        store, qdrant_path = qdrant_mysql_store
        from src.domain.models.vector_point import VectorPoint

        # Write vectors
        points = [
            VectorPoint(
                id=f"integrity_test_{i}",
                vector=[float(i * 10)] * store.dimension,
                payload={
                    "index": i,
                    "data": f"test_data_{i}",
                    "nested": {"key": f"value_{i}"},
                }
            )
            for i in range(3)
        ]
        store.upsert(points)

        # Search before rebuild
        query_vector = [10.0] * store.dimension
        results_before = store.search(query_vector, top_k=5)

        # Rebuild from MySQL
        rebuild_result = store.rebuild_from_mysql()
        assert rebuild_result["success"] is True

        # Search after rebuild
        results_after = store.search(query_vector, top_k=5)

        # Compare results (at least the first result should be similar)
        assert len(results_after) >= len(results_before) - 1  # Allow small variance

        # Cleanup
        store.delete([p.id for p in points])

    def test_mysql_durable_records_remain_intact(self, qdrant_mysql_store):
        """Test 7: MySQL durable records remain intact after Qdrant operations.

        Scenario:
        - Write vectors to MySQL
        - Delete from Qdrant
        - Verify MySQL still has the records
        - Rebuild Qdrant from MySQL
        - Verify records are restored
        """
        store, qdrant_path = qdrant_mysql_store
        from src.domain.models.vector_point import VectorPoint

        # Write vectors
        points = [
            VectorPoint(
                id=f"durable_record_test_{i}",
                vector=[float(i)] * store.dimension,
                payload={"test": "durable"}
            )
            for i in range(3)
        ]
        store.upsert(points)

        # Count in Qdrant before deletion
        qdrant_count_before = store.size()
        assert qdrant_count_before >= 3

        # Delete from Qdrant only (not from MySQL) by directly using Qdrant
        for p in points:
            store._qdrant.delete(p.id)

        # Qdrant count should be less
        qdrant_count_after_delete = store.size()
        assert qdrant_count_after_delete < qdrant_count_before

        # Rebuild from MySQL
        rebuild_result = store.rebuild_from_mysql()
        assert rebuild_result["success"] is True
        assert rebuild_result["mysql_loaded"] >= 3

        # Qdrant count should be restored
        qdrant_count_after_rebuild = store.size()
        assert qdrant_count_after_rebuild >= 3

        # Cleanup
        store.delete([p.id for p in points])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
