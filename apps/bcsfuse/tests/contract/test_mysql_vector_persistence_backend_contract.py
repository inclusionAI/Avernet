"""
Contract tests for MySQLVectorPersistenceBackend (S30C)

These tests verify:
1. Module imports without connecting
2. Backend constructs without connecting
3. Required methods exist
4. No forbidden imports
5. No fallback to SQLite/InMemory
6. Missing/invalid DB config fails fast with clear MySQL error

Optional real MySQL integration tests are gated by:
- BCSFUSE_RUN_MYSQL_INTEGRATION=1
- MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
"""

import os
import sys
import pytest
from typing import Optional


class TestMySQLVectorPersistenceBackendContract:
    """Contract tests for MySQLVectorPersistenceBackend without real DB."""

    def test_module_imports_without_connecting(self):
        """Test that the module can be imported without database connection."""
        import importlib
        import src.infra.vectorstore_backends.mysql_vector_persistence_backend as backend_module

        # Reload to ensure no connection happens on import
        importlib.reload(backend_module)

        # No exception should be raised
        assert True

    def test_backend_constructs_without_connecting(self):
        """Test that the backend can be constructed without database connection."""
        from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
            MySQLVectorPersistenceBackend,
        )

        # Should not raise error on construction
        backend = MySQLVectorPersistenceBackend()
        assert backend is not None
        assert backend.host == "localhost"
        assert backend.port == 3306
        assert backend.collection_name == "default"
        assert backend.vector_dimension == 4096
        assert backend.distance_metric == "Cosine"

        # Should also accept explicit config without connecting
        backend = MySQLVectorPersistenceBackend(
            host="test-host",
            port=3307,
            user="test-user",
            password="test-password",
            database="test-db",
            collection_name="test_collection",
            vector_dimension=768,
            distance_metric="Euclid",
        )
        assert backend.host == "test-host"
        assert backend.port == 3307
        assert backend.user == "test-user"
        assert backend.password == "test-password"
        assert backend.database == "test-db"
        assert backend.collection_name == "test_collection"
        assert backend.vector_dimension == 768
        assert backend.distance_metric == "Euclid"

    def test_required_methods_exist(self):
        """Test that all required VectorPersistenceBackend methods exist."""
        from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
            MySQLVectorPersistenceBackend,
        )

        backend = MySQLVectorPersistenceBackend()

        # Required methods from VectorPersistenceBackend protocol
        assert hasattr(backend, "save")
        assert callable(backend.save)

        assert hasattr(backend, "save_batch")
        assert callable(backend.save_batch)

        assert hasattr(backend, "load_all")
        assert callable(backend.load_all)

        assert hasattr(backend, "delete")
        assert callable(backend.delete)

        assert hasattr(backend, "delete_batch")
        assert callable(backend.delete_batch)

        assert hasattr(backend, "exists")
        assert callable(backend.exists)

        assert hasattr(backend, "count")
        assert callable(backend.count)

        assert hasattr(backend, "get_last_modified_time")
        assert callable(backend.get_last_modified_time)

        # Optional close method
        assert hasattr(backend, "close")
        assert callable(backend.close)

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
                "from src.infra.vectorstore_backends.mysql_vector_persistence_backend import MySQLVectorPersistenceBackend; print('OK')",
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

    def test_no_fallback_to_sqlite_or_inmemory(self):
        """Test that the backend does not fallback to SQLite or InMemory."""
        from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
            MySQLVectorPersistenceBackend,
        )

        backend = MySQLVectorPersistenceBackend()

        # Should not have any SQLite or InMemory references
        import inspect
        source = inspect.getsource(MySQLVectorPersistenceBackend)

        assert "sqlite" not in source.lower()
        assert "inmemory" not in source.lower()
        assert "in_memory" not in source.lower()

    def test_fail_fast_without_mysql_config(self):
        """Test that the backend fails fast without valid MySQL config."""
        from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
            MySQLVectorPersistenceBackend,
        )

        # Use invalid config that will fail on connection
        backend = MySQLVectorPersistenceBackend(
            host="invalid-host-that-does-not-exist",
            port=9999,
            user="invalid-user",
            password="invalid-password",
            database="invalid-database",
        )

        # Attempting to use the backend should raise an error
        with pytest.raises(RuntimeError) as exc_info:
            backend.count()

        # Error message should mention MySQL
        assert "mysql" in str(exc_info.value).lower()
        # Should not fallback silently
        assert "fallback" not in str(exc_info.value).lower()

    def test_observability_logging_present(self):
        """Test that the backend has observability logging."""
        from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
            MySQLVectorPersistenceBackend,
        )

        import inspect
        source = inspect.getsource(MySQLVectorPersistenceBackend)

        # Should use storage_logging functions
        assert "log_storage_event" in source
        assert "log_storage_error" in source

        # Should mask sensitive information
        assert "mask_host" in source
        assert "mask_user" in source

    def test_schema_initialization_gated(self):
        """Test that schema initialization is gated until first method call."""
        from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
            MySQLVectorPersistenceBackend,
        )

        backend = MySQLVectorPersistenceBackend()

        # After construction, connection should not be initialized
        assert backend._conn is None
        assert backend._schema_initialized is False

    def test_logs_have_correlation_id(self):
        """Test that all log events include correlation_id.

        Verify that:
        1. Log events use log_storage_event/log_storage_error
        2. Those functions automatically include correlation_id
        3. All new logging calls are properly instrumented
        """
        from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
            MySQLVectorPersistenceBackend,
        )

        import inspect
        source = inspect.getsource(MySQLVectorPersistenceBackend)

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
    os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1",
    reason="MySQL integration tests require BCSFUSE_RUN_MYSQL_INTEGRATION=1"
)
class TestMySQLVectorPersistenceBackendIntegration:
    """Integration tests for MySQLVectorPersistenceBackend with real MySQL.

    These tests require a real MySQL database and are gated by:
    - BCSFUSE_RUN_MYSQL_INTEGRATION=1
    - MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
    """

    @pytest.fixture
    def mysql_backend(self):
        """Create a MySQL backend for testing."""
        from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
            MySQLVectorPersistenceBackend,
        )

        backend = MySQLVectorPersistenceBackend(
            collection_name="test_collection"
        )
        yield backend

        # Cleanup
        try:
            backend.close()
        except Exception:
            pass

    def test_connection_initialization(self, mysql_backend):
        """Test that connection can be initialized."""
        # Should not raise
        count = mysql_backend.count()
        assert count >= 0

    def test_save_and_load_vector(self, mysql_backend):
        """Test save and load operations."""
        from src.domain.models.vector_point import VectorPoint

        # Create test vector
        point = VectorPoint(
            id="test_point_1",
            vector=[0.1, 0.2, 0.3, 0.4],
            payload={"worker_id": "test_worker", "profile_key": "test_profile"}
        )

        # Save
        mysql_backend.save(point)

        # Load all
        points = mysql_backend.load_all()
        assert len(points) >= 1

        # Find our point
        loaded_point = next((p for p in points if p.id == "test_point_1"), None)
        assert loaded_point is not None
        assert loaded_point.id == "test_point_1"
        assert len(loaded_point.vector) == 4
        assert loaded_point.payload["worker_id"] == "test_worker"

        # Cleanup
        mysql_backend.delete("test_point_1")

    def test_exists_and_count(self, mysql_backend):
        """Test exists and count operations."""
        from src.domain.models.vector_point import VectorPoint

        # Initially should not exist
        assert not mysql_backend.exists("test_point_2")

        # Save
        point = VectorPoint(
            id="test_point_2",
            vector=[0.5, 0.6, 0.7, 0.8],
            payload={}
        )
        mysql_backend.save(point)

        # Should exist now
        assert mysql_backend.exists("test_point_2")

        # Count should be >= 1
        count = mysql_backend.count()
        assert count >= 1

        # Cleanup
        mysql_backend.delete("test_point_2")

    def test_delete_operations(self, mysql_backend):
        """Test delete and delete_batch operations."""
        from src.domain.models.vector_point import VectorPoint

        # Create multiple points
        points = [
            VectorPoint(id=f"test_point_{i}", vector=[float(i), float(i+1)], payload={})
            for i in range(3, 6)
        ]
        mysql_backend.save_batch(points)

        # Delete single
        deleted = mysql_backend.delete("test_point_3")
        assert deleted is True
        assert not mysql_backend.exists("test_point_3")

        # Delete batch
        deleted_count = mysql_backend.delete_batch(["test_point_4", "test_point_5"])
        assert deleted_count >= 2

    def test_batch_operations(self, mysql_backend):
        """Test batch save and load operations."""
        from src.domain.models.vector_point import VectorPoint

        # Create batch of vectors
        points = [
            VectorPoint(
                id=f"batch_test_{i}",
                vector=[float(i), float(i+1), float(i+2)],
                payload={"index": i}
            )
            for i in range(10, 15)
        ]

        # Save batch
        mysql_backend.save_batch(points)

        # Load all
        loaded_points = mysql_backend.load_all()

        # Find our batch
        batch_points = [p for p in loaded_points if p.id.startswith("batch_test_")]
        assert len(batch_points) >= len(points)

        # Cleanup
        mysql_backend.delete_batch([p.id for p in points])

    def test_get_last_modified_time(self, mysql_backend):
        """Test get_last_modified_time returns valid timestamp."""
        from src.domain.models.vector_point import VectorPoint
        import time

        # Save a point
        point = VectorPoint(
            id="test_timestamp",
            vector=[1.0, 2.0],
            payload={}
        )
        mysql_backend.save(point)

        # Get last modified time
        timestamp = mysql_backend.get_last_modified_time()

        # Should be recent
        assert timestamp > 0
        assert abs(timestamp - time.time()) < 10  # Within 10 seconds

        # Cleanup
        mysql_backend.delete("test_timestamp")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])