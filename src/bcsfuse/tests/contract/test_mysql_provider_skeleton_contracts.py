"""
Contract tests for MySQL provider skeletons (S29B)

These tests verify that:
1. MySQL provider skeletons import successfully
2. Classes can be constructed without MySQL server
3. Classes expose all required interface methods
4. Method calls fail fast with clear S29B/S29C message
5. No forbidden internal imports
6. No fallback to InMemory/SQLite
"""

import pytest
import sys


class TestMySQLWorkerProfileBindingStoreSkeleton:
    """Contract tests for MySQLWorkerProfileBindingStore skeleton."""

    def test_import_successful(self):
        """Test that MySQLWorkerProfileBindingStore can be imported."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
            MySQLProviderNotImplementedError,
        )
        assert MySQLWorkerProfileBindingStore is not None
        assert MySQLProviderNotImplementedError is not None

    def test_construction_without_mysql(self):
        """Test that MySQLWorkerProfileBindingStore can be constructed without MySQL server."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        # Should not raise error on construction
        store = MySQLWorkerProfileBindingStore()
        assert store is not None
        assert store.host == "localhost"
        assert store.port == 3306

        # Should also accept explicit config
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

    def test_no_mysql_connection_on_import(self):
        """Test that importing the module does not connect to MySQL."""
        # Re-import should not establish connection
        # This test passes if import doesn't hang or raise connection errors
        import importlib
        import src.infra.public.stores.mysql_worker_profile_binding_store as store_module
        importlib.reload(store_module)

        # No exception should be raised
        assert True

    def test_exposes_bind_profile_method(self):
        """Test that bind_profile method exists."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )
        from src.domain.models.worker_source_info import WorkerSourceType

        store = MySQLWorkerProfileBindingStore()
        assert hasattr(store, "bind_profile")
        assert callable(store.bind_profile)

        # S29C: Method is now implemented and will fail with connection error
        # when MySQL is not available (not MySQLProviderNotImplementedError)
        with pytest.raises(Exception) as exc_info:
            store.bind_profile("worker-1", "profile-1", WorkerSourceType.API)

        # Should be a MySQL connection error, not skeleton error
        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_unbind_profile_method(self):
        """Test that unbind_profile method exists."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        store = MySQLWorkerProfileBindingStore()
        assert hasattr(store, "unbind_profile")
        assert callable(store.unbind_profile)

        # S29C: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.unbind_profile("worker-1", "profile-1")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_get_active_binding_method(self):
        """Test that get_active_binding method exists."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        store = MySQLWorkerProfileBindingStore()
        assert hasattr(store, "get_active_binding")
        assert callable(store.get_active_binding)

        # S29C: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.get_active_binding("worker-1")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_set_active_profile_method(self):
        """Test that set_active_profile method exists."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        store = MySQLWorkerProfileBindingStore()
        assert hasattr(store, "set_active_profile")
        assert callable(store.set_active_profile)

        # S29C: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.set_active_profile("worker-1", "profile-1")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_list_bindings_by_worker_method(self):
        """Test that list_bindings_by_worker method exists."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        store = MySQLWorkerProfileBindingStore()
        assert hasattr(store, "list_bindings_by_worker")
        assert callable(store.list_bindings_by_worker)

        # S29C: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.list_bindings_by_worker("worker-1")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_get_binding_by_profile_key_method(self):
        """Test that get_binding_by_profile_key method exists."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        store = MySQLWorkerProfileBindingStore()
        assert hasattr(store, "get_binding_by_profile_key")
        assert callable(store.get_binding_by_profile_key)

        # S29C: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.get_binding_by_profile_key("profile-1")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_fail_fast_without_mysql_config(self):
        """Test that methods fail fast when DB not configured (S29C)."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )
        from src.domain.models.worker_source_info import WorkerSourceType

        store = MySQLWorkerProfileBindingStore()

        # S29C: All methods should fail with clear MySQL connection error
        # (not MySQLProviderNotImplementedError, as methods are now implemented)
        with pytest.raises(RuntimeError) as exc_info:
            store.bind_profile("w", "p", WorkerSourceType.API)
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.unbind_profile("w", "p")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.get_active_binding("w")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.set_active_profile("w", "p")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.list_bindings_by_worker("w")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.get_binding_by_profile_key("p")
        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_no_fallback_to_inmemory(self):
        """Test that MySQL provider does not fallback to InMemory."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        store = MySQLWorkerProfileBindingStore()

        # S29C: Should fail with MySQL connection error, not InMemory/SQLite fallback
        with pytest.raises(RuntimeError) as exc_info:
            store.get_active_binding("worker-1")

        # Error message should not mention InMemory or SQLite fallback
        error_msg = str(exc_info.value).lower()
        assert "inmemory" not in error_msg
        assert "sqlite" not in error_msg
        assert "fallback" not in error_msg

        # Should mention MySQL connection failure
        assert "mysql" in error_msg or "connection" in error_msg

    def test_no_forbidden_internal_imports(self):
        """Test that provider does not import forbidden internal dependencies."""
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from src.infra.public.stores.mysql_worker_profile_binding_store import MySQLWorkerProfileBindingStore; print('OK')",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Import failed: {result.stderr}"
        assert "OK" in result.stdout

        # Check for forbidden imports in error output
        forbidden = [
            "bcsfuse_internal",
            "sofapy",
            "mist",
            "layotto",
            "zdas",
        ]
        for keyword in forbidden:
            assert keyword not in result.stdout.lower()
            assert keyword not in result.stderr.lower()


class TestMySQLFusedProfileStoreSkeleton:
    """Contract tests for MySQLFusedProfileStore skeleton."""

    def test_import_successful(self):
        """Test that MySQLFusedProfileStore can be imported."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
            MySQLProviderNotImplementedError,
        )
        assert MySQLFusedProfileStore is not None
        assert MySQLProviderNotImplementedError is not None

    def test_construction_without_mysql(self):
        """Test that MySQLFusedProfileStore can be constructed without MySQL server."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        # Should not raise error on construction
        store = MySQLFusedProfileStore()
        assert store is not None
        assert store.host == "localhost"
        assert store.port == 3306

        # Should also accept explicit config
        store = MySQLFusedProfileStore(
            host="test-host",
            port=3307,
            user="test-user",
            password="test-password",
            database="test-db",
        )
        assert store.host == "test-host"
        assert store.port == 3307

    def test_no_mysql_connection_on_import(self):
        """Test that importing the module does not connect to MySQL."""
        import importlib
        import src.infra.public.stores.mysql_fused_profile_store as store_module
        importlib.reload(store_module)

        # No exception should be raised
        assert True

    def test_exposes_save_method(self):
        """Test that save method exists."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()
        assert hasattr(store, "save")
        assert callable(store.save)

        # S29E: Method is now implemented and will fail with connection error
        # when MySQL is not available (not MySQLProviderNotImplementedError)
        with pytest.raises(Exception) as exc_info:
            store.save(None)

        # Should be a MySQL connection error, not skeleton error
        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_find_by_key_method(self):
        """Test that find_by_key method exists."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()
        assert hasattr(store, "find_by_key")
        assert callable(store.find_by_key)

        # S29E: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.find_by_key("fusion-1")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_find_by_participant_method(self):
        """Test that find_by_participant method exists."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()
        assert hasattr(store, "find_by_participant")
        assert callable(store.find_by_participant)

        # S29E: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.find_by_participant("participant-1")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_find_by_group_method(self):
        """Test that find_by_group method exists."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()
        assert hasattr(store, "find_by_group")
        assert callable(store.find_by_group)

        # S29E: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.find_by_group("group-1")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_append_turn_method(self):
        """Test that append_turn method exists."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()
        assert hasattr(store, "append_turn")
        assert callable(store.append_turn)

        # S29E: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.append_turn("fusion-1", None)

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_get_conversation_method(self):
        """Test that get_conversation method exists."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()
        assert hasattr(store, "get_conversation")
        assert callable(store.get_conversation)

        # S29E: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.get_conversation("fusion-1")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_update_status_method(self):
        """Test that update_status method exists."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()
        assert hasattr(store, "update_status")
        assert callable(store.update_status)

        # S29E: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.update_status("fusion-1", "completed")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_exists_method(self):
        """Test that exists method exists."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()
        assert hasattr(store, "exists")
        assert callable(store.exists)

        # S29E: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.exists("fusion-1")

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_exposes_update_method(self):
        """Test that update method exists."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()
        assert hasattr(store, "update")
        assert callable(store.update)

        # S29E: Method is now implemented and will fail with connection error
        with pytest.raises(Exception) as exc_info:
            store.update(None)

        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_fail_fast_without_mysql_config(self):
        """Test that methods fail fast when DB not configured (S29E)."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()

        # S29E: All methods should fail with clear MySQL connection error
        # (not MySQLProviderNotImplementedError, as methods are now implemented)
        with pytest.raises(RuntimeError) as exc_info:
            store.save(None)
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.find_by_key("fusion-1")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.find_by_participant("participant-1")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.find_by_group("group-1")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.append_turn("fusion-1", None)
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.get_conversation("fusion-1")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.update_status("fusion-1", "completed")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.exists("fusion-1")
        assert "Failed to connect to MySQL" in str(exc_info.value)

        with pytest.raises(RuntimeError) as exc_info:
            store.update(None)
        assert "Failed to connect to MySQL" in str(exc_info.value)

    def test_no_fallback_to_inmemory(self):
        """Test that MySQL provider does not fallback to InMemory."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()

        # S29E: Should fail with MySQL connection error, not InMemory/SQLite fallback
        with pytest.raises(RuntimeError) as exc_info:
            store.find_by_key("fusion-1")

        # Error message should not mention InMemory or SQLite fallback
        error_msg = str(exc_info.value).lower()
        assert "inmemory" not in error_msg
        assert "sqlite" not in error_msg
        assert "fallback" not in error_msg

        # Should mention MySQL connection failure
        assert "mysql" in error_msg or "connection" in error_msg

    def test_no_forbidden_internal_imports(self):
        """Test that provider does not import forbidden internal dependencies."""
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from src.infra.public.stores.mysql_fused_profile_store import MySQLFusedProfileStore; print('OK')",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Import failed: {result.stderr}"
        assert "OK" in result.stdout

        # Check for forbidden imports in error output
        forbidden = [
            "bcsfuse_internal",
            "sofapy",
            "mist",
            "layotto",
            "zdas",
        ]
        for keyword in forbidden:
            assert keyword not in result.stdout.lower()
            assert keyword not in result.stderr.lower()


class TestMySQLProviderSkeletonsNoRealMySQLIntegration:
    """Tests verifying that skeletons do not require real MySQL."""

    def test_workers_without_mysql_server(self):
        """Test that both providers can be imported and constructed without MySQL server."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        # Should not require MySQL server to be running
        binding_store = MySQLWorkerProfileBindingStore()
        fused_store = MySQLFusedProfileStore()

        assert binding_store is not None
        assert fused_store is not None

    def test_methods_fail_with_clear_messages(self):
        """Test that all methods fail with clear messages."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )
        from src.domain.models.worker_source_info import WorkerSourceType

        binding_store = MySQLWorkerProfileBindingStore()
        fused_store = MySQLFusedProfileStore()

        # S29C: MySQLWorkerProfileBindingStore is fully implemented
        # Methods should fail with clear MySQL connection error
        with pytest.raises(RuntimeError) as exc_info:
            binding_store.bind_profile("w", "p", WorkerSourceType.API)
        error_msg = str(exc_info.value)
        assert "Failed to connect to MySQL" in error_msg

        # S29E: MySQLFusedProfileStore is now fully implemented
        # Methods should fail with clear MySQL connection error
        with pytest.raises(RuntimeError) as exc_info:
            fused_store.save(None)
        error_msg = str(exc_info.value)
        assert "Failed to connect to MySQL" in error_msg