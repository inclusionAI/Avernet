"""
Contract tests for MySQLWorkerProfileBindingStore (S29C)

These tests verify:
1. Module imports without connecting
2. Store constructs without connecting
3. Required methods exist
4. No forbidden imports
5. No fallback to InMemory/SQLite
6. Missing/invalid DB config fails fast with clear MySQL error

Optional real MySQL integration tests are gated by:
- BCSFUSE_RUN_MYSQL_INTEGRATION=1
- MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
"""

import os
import sys
import pytest
from typing import Optional


class TestMySQLWorkerProfileBindingStoreContract:
    """Contract tests for MySQLWorkerProfileBindingStore without real DB."""

    def test_module_imports_without_connecting(self):
        """Test that the module can be imported without database connection."""
        import importlib
        import src.infra.public.stores.mysql_worker_profile_binding_store as store_module

        # Reload to ensure no connection happens on import
        importlib.reload(store_module)

        # No exception should be raised
        assert True

    def test_store_constructs_without_connecting(self):
        """Test that the store can be constructed without database connection."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        # Should not raise error on construction
        store = MySQLWorkerProfileBindingStore()
        assert store is not None
        assert store.host == "localhost"
        assert store.port == 3306

        # Should also accept explicit config without connecting
        store = MySQLWorkerProfileBindingStore(
            host="test-host",
            port=3307,
            user="test-user",
            password="test-password",
            database="test-db",
        )
        assert store.host == "test-host"
        assert store.port == 3307
        assert store.user == "test-user"
        assert store.password == "test-password"
        assert store.database == "test-db"

    def test_required_methods_exist(self):
        """Test that all required methods exist."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        store = MySQLWorkerProfileBindingStore()

        # Required methods from WorkerProfileBindingStoreAdapter protocol
        assert hasattr(store, "bind_profile")
        assert callable(store.bind_profile)

        assert hasattr(store, "unbind_profile")
        assert callable(store.unbind_profile)

        assert hasattr(store, "get_active_binding")
        assert callable(store.get_active_binding)

        assert hasattr(store, "set_active_profile")
        assert callable(store.set_active_profile)

        assert hasattr(store, "list_bindings_by_worker")
        assert callable(store.list_bindings_by_worker)

        assert hasattr(store, "get_binding_by_profile_key")
        assert callable(store.get_binding_by_profile_key)

        # Optional close method
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
                "from src.infra.public.stores.mysql_worker_profile_binding_store import MySQLWorkerProfileBindingStore; print('OK')",
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

    def test_no_fallback_to_inmemory_or_sqlite(self):
        """Test that the store does not fallback to InMemory or SQLite."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )
        from src.domain.models.worker_source_info import WorkerSourceType

        store = MySQLWorkerProfileBindingStore()

        # Should fail with MySQL connection error, not InMemory/SQLite fallback
        with pytest.raises(RuntimeError) as exc_info:
            store.bind_profile("worker-1", "profile-1", WorkerSourceType.API)

        error_msg = str(exc_info.value).lower()

        # Should not mention InMemory or SQLite
        assert "inmemory" not in error_msg
        assert "sqlite" not in error_msg
        assert "fallback" not in error_msg

        # Should mention MySQL connection failure
        assert "mysql" in error_msg or "connection" in error_msg

    def test_fail_fast_without_mysql_config(self):
        """Test that methods fail fast with clear MySQL error when DB is not configured."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )
        from src.domain.models.worker_source_info import WorkerSourceType

        store = MySQLWorkerProfileBindingStore()

        # All methods should fail with clear MySQL connection error
        with pytest.raises(RuntimeError) as exc_info:
            store.bind_profile("worker-1", "profile-1", WorkerSourceType.API)
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.unbind_profile("worker-1", "profile-1")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.get_active_binding("worker-1")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.set_active_profile("worker-1", "profile-1")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.list_bindings_by_worker("worker-1")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.get_binding_by_profile_key("profile-1")
        assert "Failed to connect to MySQL" in str(exc_info.value)


@pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1",
    reason="BCSFUSE_RUN_MYSQL_INTEGRATION not set to 1"
)
class TestMySQLWorkerProfileBindingStoreIntegration:
    """Integration tests with real MySQL database (gated by environment variable)."""

    @pytest.fixture
    def mysql_store(self):
        """Create MySQL store with configuration from environment."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        # Get MySQL configuration from environment
        host = os.getenv("MYSQL_HOST", "localhost")
        port = int(os.getenv("MYSQL_PORT", "3306"))
        user = os.getenv("MYSQL_USER", "root")
        password = os.getenv("MYSQL_PASSWORD", "")
        database = os.getenv("MYSQL_DATABASE", "bcsfuse_test")

        store = MySQLWorkerProfileBindingStore(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
        )

        yield store

        # Cleanup
        try:
            store.close()
        except Exception:
            pass

    def test_bind_profile_creates_binding(self, mysql_store):
        """Test that bind_profile creates a binding."""
        from src.domain.models.worker_source_info import WorkerSourceType

        worker_id = "test-worker-1"
        profile_key = "test-profile-1"

        binding = mysql_store.bind_profile(worker_id, profile_key, WorkerSourceType.API)

        assert binding is not None
        assert binding.worker_id == worker_id
        assert binding.profile_key == profile_key
        assert binding.source_type == WorkerSourceType.API
        assert binding.is_active is True
        assert binding.bound_at is not None

    def test_list_bindings_by_worker_returns_binding(self, mysql_store):
        """Test that list_bindings_by_worker returns bindings."""
        from src.domain.models.worker_source_info import WorkerSourceType

        worker_id = "test-worker-2"
        profile_key = "test-profile-2"

        mysql_store.bind_profile(worker_id, profile_key, WorkerSourceType.API)

        bindings = mysql_store.list_bindings_by_worker(worker_id)

        assert len(bindings) > 0
        assert any(b.worker_id == worker_id and b.profile_key == profile_key for b in bindings)

    def test_get_binding_by_profile_key_returns_binding(self, mysql_store):
        """Test that get_binding_by_profile_key returns binding."""
        from src.domain.models.worker_source_info import WorkerSourceType

        worker_id = "test-worker-3"
        profile_key = "test-profile-3"

        mysql_store.bind_profile(worker_id, profile_key, WorkerSourceType.API)

        binding = mysql_store.get_binding_by_profile_key(profile_key)

        assert binding is not None
        assert binding.worker_id == worker_id
        assert binding.profile_key == profile_key

    def test_set_active_profile_activates_selected_and_deactivates_previous(self, mysql_store):
        """Test that set_active_profile atomically activates selected and deactivates previous."""
        from src.domain.models.worker_source_info import WorkerSourceType

        worker_id = "test-worker-4"
        profile_key_1 = "test-profile-4a"
        profile_key_2 = "test-profile-4b"

        # Bind first profile
        mysql_store.bind_profile(worker_id, profile_key_1, WorkerSourceType.API)
        binding_1 = mysql_store.get_active_binding(worker_id)
        assert binding_1 is not None
        assert binding_1.profile_key == profile_key_1
        assert binding_1.is_active is True

        # Bind second profile (should deactivate first)
        mysql_store.bind_profile(worker_id, profile_key_2, WorkerSourceType.API)
        binding_2 = mysql_store.get_active_binding(worker_id)
        assert binding_2 is not None
        assert binding_2.profile_key == profile_key_2
        assert binding_2.is_active is True

        # Reactivate first profile using set_active_profile
        success = mysql_store.set_active_profile(worker_id, profile_key_1)
        assert success is True

        # Verify first profile is active, second is not
        active_binding = mysql_store.get_active_binding(worker_id)
        assert active_binding is not None
        assert active_binding.profile_key == profile_key_1
        assert active_binding.is_active is True

        # Verify second profile is inactive
        binding_2_after = mysql_store.get_binding_by_profile_key(profile_key_2)
        assert binding_2_after is None  # get_binding_by_profile_key only returns active

    def test_get_active_binding_returns_active_binding(self, mysql_store):
        """Test that get_active_binding returns the active binding."""
        from src.domain.models.worker_source_info import WorkerSourceType

        worker_id = "test-worker-5"
        profile_key = "test-profile-5"

        mysql_store.bind_profile(worker_id, profile_key, WorkerSourceType.API)

        binding = mysql_store.get_active_binding(worker_id)

        assert binding is not None
        assert binding.worker_id == worker_id
        assert binding.profile_key == profile_key
        assert binding.is_active is True

    def test_unbind_profile_removes_binding(self, mysql_store):
        """Test that unbind_profile deactivates binding."""
        from src.domain.models.worker_source_info import WorkerSourceType

        worker_id = "test-worker-6"
        profile_key = "test-profile-6"

        mysql_store.bind_profile(worker_id, profile_key, WorkerSourceType.API)

        # Unbind
        success = mysql_store.unbind_profile(worker_id, profile_key)
        assert success is True

        # Verify no active binding
        active_binding = mysql_store.get_active_binding(worker_id)
        assert active_binding is None

        # Verify binding exists but is inactive (list_bindings_by_worker shows all)
        bindings = mysql_store.list_bindings_by_worker(worker_id)
        unbound_binding = [b for b in bindings if b.profile_key == profile_key][0]
        assert unbound_binding.is_active is False
        assert unbound_binding.unbound_at is not None

    def test_duplicate_binding_does_not_create_duplicate_rows(self, mysql_store):
        """Test that binding the same profile twice does not create duplicate rows."""
        from src.domain.models.worker_source_info import WorkerSourceType

        worker_id = "test-worker-7"
        profile_key = "test-profile-7"

        # Bind same profile twice
        binding_1 = mysql_store.bind_profile(worker_id, profile_key, WorkerSourceType.API)
        binding_2 = mysql_store.bind_profile(worker_id, profile_key, WorkerSourceType.FILE)

        # Both should succeed (update existing)
        assert binding_1 is not None
        assert binding_2 is not None

        # Should only have one binding
        bindings = mysql_store.list_bindings_by_worker(worker_id)
        profile_bindings = [b for b in bindings if b.profile_key == profile_key]
        assert len(profile_bindings) == 1

        # Latest binding should be active
        assert profile_bindings[0].is_active is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])