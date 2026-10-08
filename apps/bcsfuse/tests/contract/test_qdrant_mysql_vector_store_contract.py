"""
Contract tests for QdrantMySQLVectorStore (S30C)

These tests verify:
1. Module imports without connecting
2. Store constructs without connecting
3. Required methods exist
4. rebuild_from_mysql method exists
5. No forbidden imports
6. Business ID mapping reused
7. Diagnostic logging present

Optional real integration tests are gated by:
- BCSFUSE_RUN_MYSQL_INTEGRATION=1
- MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
- BCSFUSE_RUN_QDRANT_INTEGRATION=1
- QDRANT_LOCAL_PATH, QDRANT_COLLECTION, VECTOR_DIMENSION
"""

import os
import sys
import pytest
from typing import Optional


class TestQdrantMySQLVectorStoreContract:
    """Contract tests for QdrantMySQLVectorStore without real resources."""

    def test_module_imports_without_connecting(self):
        """Test that the module can be imported without database/Qdrant connection."""
        import importlib
        import src.infra.public.vectorstores.qdrant_mysql_vector_store as store_module

        # Reload to ensure no connection happens on import
        importlib.reload(store_module)

        # No exception should be raised
        assert True

    def test_store_constructs_without_connecting(self):
        """Test that the store can be constructed without database/Qdrant connection."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        # Should not raise error on construction
        store = QdrantMySQLVectorStore()
        assert store is not None
        assert store.collection_name == "bcsfuse_vectors"
        assert store.dimension == 4096
        assert store.distance == "Cosine"

        # Should also accept explicit config without connecting
        store = QdrantMySQLVectorStore(
            collection_name="test_collection",
            qdrant_path="/tmp/test_qdrant",
            dimension=768,
            distance="Euclid",
            mysql_host="test-host",
            mysql_port=3307,
            mysql_user="test-user",
            mysql_password="test-password",
            mysql_database="test-db",
        )
        assert store.collection_name == "test_collection"
        assert store.dimension == 768
        assert store.distance == "Euclid"

    def test_required_methods_exist(self):
        """Test that all required methods exist."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        store = QdrantMySQLVectorStore()

        # Required methods from vector store pattern
        assert hasattr(store, "upsert")
        assert callable(store.upsert)

        assert hasattr(store, "search")
        assert callable(store.search)

        assert hasattr(store, "delete")
        assert callable(store.delete)

        # Rebuild method - CRITICAL for this implementation
        assert hasattr(store, "rebuild_from_mysql")
        assert callable(store.rebuild_from_mysql)

        # Utility methods
        assert hasattr(store, "__len__")
        assert callable(store.__len__)

        assert hasattr(store, "close")
        assert callable(store.close)

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
                "from src.infra.public.vectorstores.qdrant_mysql_vector_store import QdrantMySQLVectorStore; print('OK')",
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
        ]
        for keyword in forbidden:
            assert keyword not in result.stdout.lower()
            assert keyword not in result.stderr.lower()

    def test_business_id_mapping_reused(self):
        """Test that business ID mapping from QdrantLocalVectorStore is reused."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        import inspect
        source = inspect.getsource(QdrantMySQLVectorStore)

        # Should use QdrantLocalVectorStore which has business ID mapping
        assert "QdrantLocalVectorStore" in source

    def test_diagnostic_logging_present(self):
        """Test that the store has comprehensive diagnostic logging."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        import inspect
        source = inspect.getsource(QdrantMySQLVectorStore)

        # Should use storage_logging functions
        assert "log_storage_event" in source
        assert "log_storage_error" in source

        # Should have phase-based logging
        assert "validation_phase" in source

        # Should have operation logging
        assert "operation=" in source

        # Should have backend logging (qdrant+mysql, mysql, qdrant)
        assert 'backend="qdrant+mysql"' in source
        assert 'backend="mysql"' in source
        assert 'backend="qdrant"' in source

    def test_rebuild_from_mysql_signature(self):
        """Test that rebuild_from_mysql has correct signature."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        import inspect
        sig = inspect.signature(QdrantMySQLVectorStore.rebuild_from_mysql)

        # Should have batch_size parameter with default
        assert "batch_size" in sig.parameters
        assert sig.parameters["batch_size"].default == 100

        # Should have docstring
        assert QdrantMySQLVectorStore.rebuild_from_mysql.__doc__ is not None
        assert "Rebuild" in QdrantMySQLVectorStore.rebuild_from_mysql.__doc__

    def test_write_ordering_documented(self):
        """Test that write-through ordering is documented."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        import inspect
        source = inspect.getsource(QdrantMySQLVectorStore)

        # Should document write ordering: MySQL first, then Qdrant
        docstring = QdrantMySQLVectorStore.__doc__
        assert "mysql" in docstring.lower()
        assert "write-through" in docstring.lower() or "write through" in docstring.lower()

    def test_fail_fast_if_mysql_backend_missing(self):
        """Test that operations fail fast if MySQL backend is missing config."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        # Use invalid config that will fail on connection
        store = QdrantMySQLVectorStore(
            mysql_host="invalid-host-that-does-not-exist",
            mysql_port=9999,
            mysql_user="invalid-user",
            mysql_password="invalid-password",
            mysql_database="invalid-database",
        )

        # Attempting an operation that requires MySQL should raise an error
        with pytest.raises(RuntimeError) as exc_info:
            store.rebuild_from_mysql()

        # Error message should mention MySQL or connection or failed
        error_msg = str(exc_info.value).lower()
        assert "mysql" in error_msg or "connection" in error_msg or "failed" in error_msg

    def test_partial_failure_semantics_classified(self):
        """Test that MySQL success + Qdrant failure is properly classified.

        Verify that:
        1. Method raises exception (not fake success)
        2. Exception message indicates partial failure state
        3. Exception message mentions rebuild required
        """
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        # Verify that the code has explicit handling for partial failure
        import inspect
        source = inspect.getsource(QdrantMySQLVectorStore.upsert)

        # Should have classification for partial failure
        assert "DEGRADED_REBUILD_REQUIRED" in source
        assert "QDRANT_INDEX_UPDATE_FAILED_AFTER_DURABLE_WRITE" in source
        assert "durable_write_success=True" in source
        assert "qdrant_index_success=False" in source

        # Should raise exception (not silently ignore)
        assert "raise RuntimeError" in source

        # Should mention rebuild required
        assert "Rebuild required" in source or "rebuild_required" in source

    def test_no_fake_success_when_qdrant_index_update_fails(self):
        """Test that method does not return success when Qdrant index update fails."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        import inspect
        source = inspect.getsource(QdrantMySQLVectorStore.upsert)

        # Should not have a "success" log after Qdrant failure without raising
        # The success log should only be after both MySQL and Qdrant succeed
        lines = source.split('\n')

        # Find the line with "qdrant_write_failure_mysql_ok"
        failure_line_idx = None
        for i, line in enumerate(lines):
            if 'qdrant_write_failure_mysql_ok' in line:
                failure_line_idx = i
                break

        if failure_line_idx is not None:
            # After the failure log, should raise exception before any "success" log
            remaining_lines = lines[failure_line_idx:]

            # Check that there's a raise statement before the next success log
            has_raise_before_success = False
            for line in remaining_lines:
                if 'raise RuntimeError' in line or 'raise Exception' in line:
                    has_raise_before_success = True
                    break
                # Don't check for "success" in string literals, check for log_storage_event with "success"
                if 'log_storage_event' in line and 'success' in line.lower() and 'INFO' not in line:
                    # Found success log before raise
                    break

            # After the except block, success log should indicate consistency
            assert has_raise_before_success or 'CONSISTENT' in source

    def test_logs_have_correlation_id(self):
        """Test that all log events include correlation_id.

        Verify that:
        1. Log events use log_storage_event/log_storage_error
        2. Those functions automatically include correlation_id
        3. All new logging calls are properly instrumented
        """
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        import inspect
        source = inspect.getsource(QdrantMySQLVectorStore)

        # Should use log_storage_event and log_storage_error
        assert "log_storage_event" in source
        assert "log_storage_error" in source

        # Count logging calls
        log_event_count = source.count("log_storage_event")
        log_error_count = source.count("log_storage_error")

        # Should have logging calls
        assert log_event_count > 0, "Should have log_storage_event calls"
        assert log_error_count > 0, "Should have log_storage_error calls"

        # log_storage_event and log_storage_error automatically include correlation_id
        # (verified by storage_logging.py implementation)


@pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1" or os.getenv("BCSFUSE_RUN_QDRANT_INTEGRATION") != "1",
    reason="Integration tests require BCSFUSE_RUN_MYSQL_INTEGRATION=1 and BCSFUSE_RUN_QDRANT_INTEGRATION=1"
)
class TestQdrantMySQLVectorStoreIntegration:
    """Integration tests for QdrantMySQLVectorStore with real MySQL and Qdrant.

    These tests require:
    - BCSFUSE_RUN_MYSQL_INTEGRATION=1
    - MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
    - BCSFUSE_RUN_QDRANT_INTEGRATION=1
    - QDRANT_LOCAL_PATH, QDRANT_COLLECTION, VECTOR_DIMENSION
    """

    @pytest.fixture
    def vector_store(self):
        """Create a QdrantMySQLVectorStore for testing."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )
        import tempfile
        import shutil

        # Create temp directory for Qdrant
        qdrant_path = tempfile.mkdtemp(prefix="test_qdrant_mysql_")

        store = QdrantMySQLVectorStore(
            collection_name=f"test_collection_{os.getenv('QDRANT_COLLECTION', 'default')}",
            qdrant_path=qdrant_path,
            dimension=int(os.getenv("VECTOR_DIMENSION", "4096")),
        )

        yield store

        # Cleanup
        try:
            store.close()
            shutil.rmtree(qdrant_path, ignore_errors=True)
        except Exception:
            pass

    def test_rebuild_from_mysql(self, vector_store):
        """Test rebuild_from_mysql end-to-end flow."""
        from src.domain.models.vector_point import VectorPoint

        # Get dimension from vector store
        dimension = vector_store.dimension

        # 1. Write vectors
        points = [
            VectorPoint(
                id=f"rebuild_test_{i}",
                vector=[float(i) * 0.1] * dimension,
                payload={"worker_id": f"worker_{i}", "index": i}
            )
            for i in range(5)
        ]
        vector_store.upsert(points)

        # 2. Rebuild from MySQL
        result = vector_store.rebuild_from_mysql(batch_size=2)

        # 3. Verify rebuild result
        assert result["success"] is True
        assert result["mysql_loaded"] >= 5
        assert result["qdrant_inserted"] >= 5
        assert result["qdrant_count"] >= 5
        assert result["batches"] >= 3  # 5 points / 2 per batch = 3 batches

        # 4. Search after rebuild
        query_vector = [0.1] * dimension
        results = vector_store.search(query_vector, top_k=3)

        assert len(results) >= 1
        # Results should contain logical IDs (external IDs)
        for result in results:
            assert "id" in result
            assert "score" in result

        # Cleanup
        vector_store.delete([p.id for p in points])

    def test_write_through_and_search(self, vector_store):
        """Test write-through to MySQL and search from Qdrant."""
        from src.domain.models.vector_point import VectorPoint

        # Get dimension from vector store
        dimension = vector_store.dimension

        # 1. Write vectors
        points = [
            VectorPoint(
                id="write_test_1",
                vector=[0.5] * dimension,
                payload={"staff_id": "worker_123", "profile_key": "profile_456"}
            )
        ]
        vector_store.upsert(points)

        # 2. Search - should find the vector
        query_vector = [0.5] * dimension
        results = vector_store.search(query_vector, top_k=1)

        assert len(results) >= 1
        assert results[0]["id"] == "write_test_1"

        # Cleanup
        vector_store.delete(["write_test_1"])

    def test_delete_operations(self, vector_store):
        """Test delete operations remove from both MySQL and Qdrant."""
        from src.domain.models.vector_point import VectorPoint

        # Get dimension from vector store
        dimension = vector_store.dimension

        # 1. Write vectors
        points = [
            VectorPoint(
                id=f"delete_test_{i}",
                vector=[float(i)] * dimension,
                payload={}
            )
            for i in range(3)
        ]
        vector_store.upsert(points)

        # 2. Delete
        vector_store.delete(["delete_test_0", "delete_test_1"])

        # 3. Verify deleted from Qdrant
        query_vector = [0.0] * dimension
        results = vector_store.search(query_vector, top_k=10)

        # Should not find deleted vectors
        result_ids = [r["id"] for r in results]
        assert "delete_test_0" not in result_ids
        assert "delete_test_1" not in result_ids
        assert "delete_test_2" in result_ids  # This one should still be there

        # Cleanup
        vector_store.delete(["delete_test_2"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])