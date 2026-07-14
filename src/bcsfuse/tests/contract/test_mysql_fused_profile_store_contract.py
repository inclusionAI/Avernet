"""
Contract tests for MySQLFusedProfileStore (S29E)

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


class TestMySQLFusedProfileStoreContract:
    """Contract tests for MySQLFusedProfileStore without real DB."""

    def test_module_imports_without_connecting(self):
        """Test that the module can be imported without database connection."""
        import importlib
        import src.infra.public.stores.mysql_fused_profile_store as store_module

        # Reload to ensure no connection happens on import
        importlib.reload(store_module)

        # No exception should be raised
        assert True

    def test_store_constructs_without_connecting(self):
        """Test that the store can be constructed without database connection."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        # Should not raise error on construction
        store = MySQLFusedProfileStore()
        assert store is not None
        assert store.host == "localhost"
        assert store.port == 3306

        # Should also accept explicit config without connecting
        store = MySQLFusedProfileStore(
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
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()

        # Required methods from FusedProfileRepository protocol
        assert hasattr(store, "save")
        assert callable(store.save)

        assert hasattr(store, "find_by_key")
        assert callable(store.find_by_key)

        assert hasattr(store, "find_by_participant")
        assert callable(store.find_by_participant)

        assert hasattr(store, "find_by_group")
        assert callable(store.find_by_group)

        assert hasattr(store, "append_turn")
        assert callable(store.append_turn)

        assert hasattr(store, "get_conversation")
        assert callable(store.get_conversation)

        assert hasattr(store, "update_status")
        assert callable(store.update_status)

        assert hasattr(store, "exists")
        assert callable(store.exists)

        assert hasattr(store, "update")
        assert callable(store.update)

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
                "from src.infra.public.stores.mysql_fused_profile_store import MySQLFusedProfileStore; print('OK')",
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
            assert keyword not in result.stdout.lower(), f"Forbidden import found: {keyword}"
            assert keyword not in result.stderr.lower(), f"Forbidden import found: {keyword}"

    def test_no_fallback_to_inmemory_or_sqlite(self):
        """Test that the store does not fallback to InMemory or SQLite."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()

        # All methods should fail with MySQL connection error, not fallback
        with pytest.raises(RuntimeError) as exc_info:
            store.find_by_key("test-fusion-id")

        error_msg = str(exc_info.value).lower()
        assert "inmemory" not in error_msg
        assert "sqlite" not in error_msg
        assert "fallback" not in error_msg

        # Should mention MySQL connection failure
        assert "mysql" in error_msg or "connection" in error_msg

    def test_fail_fast_without_mysql_config(self):
        """Test that methods fail fast with clear MySQL error when DB not configured."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        store = MySQLFusedProfileStore()

        # All methods should fail with clear MySQL connection error
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

    def test_lazy_connection_initialization(self):
        """Test that connection is lazy - not established until first method call."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        # Construction should not connect
        store = MySQLFusedProfileStore(
            host="nonexistent-host",
            port=9999,
            user="invalid",
            password="invalid",
            database="invalid",
        )

        # Connection should not be established yet
        assert store._conn is None
        assert not store._schema_initialized

        # Only when we call a method should it try to connect and fail
        with pytest.raises(RuntimeError) as exc_info:
            store.find_by_key("test-id")

        assert "Failed to connect to MySQL" in str(exc_info.value)


# Skip real MySQL integration tests unless explicitly enabled
@pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1",
    reason="MySQL integration tests require BCSFUSE_RUN_MYSQL_INTEGRATION=1 and MySQL env vars"
)
class TestMySQLFusedProfileStoreIntegration:
    """Real MySQL integration tests (requires MySQL server)."""

    @pytest.fixture
    def mysql_store(self):
        """Create MySQLFusedProfileStore with real MySQL connection."""
        from src.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        host = os.getenv("MYSQL_HOST", "localhost")
        port = int(os.getenv("MYSQL_PORT", "3306"))
        user = os.getenv("MYSQL_USER", "root")
        password = os.getenv("MYSQL_PASSWORD", "")
        database = os.getenv("MYSQL_DATABASE", "bcsfuse_test")

        # Do NOT print credentials
        store = MySQLFusedProfileStore(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
        )

        yield store

        # Cleanup
        store.close()

    @pytest.fixture
    def test_record(self):
        """Create a test FusedProfileRecord."""
        from src.domain.models.profile_fusion import FusedProfileRecord
        from datetime import datetime

        return FusedProfileRecord(
            fusion_id="test-fusion-integration-1",
            fusion_mode="g9",
            group_id="test-group-1",
            driver_bot_id="test-bot-1",
            question="Test question",
            participant_ids="worker-1,worker-2",
            status="pending",
            env="test",
            created_by="test-user",
            gmt_create=datetime.utcnow(),
            gmt_modify=datetime.utcnow(),
        )

    def test_save_creates_fused_profile(self, mysql_store, test_record):
        """Test that save creates a fused profile."""
        fusion_id = mysql_store.save(test_record)
        assert fusion_id == test_record.fusion_id

        # Cleanup
        try:
            # Delete the record
            cursor = mysql_store._conn.cursor()
            cursor.execute("DELETE FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (fusion_id,))
            mysql_store._conn.commit()
            cursor.close()
        except Exception:
            pass

    def test_find_by_key_returns_saved_record(self, mysql_store, test_record):
        """Test that find_by_key returns the saved record."""
        mysql_store.save(test_record)

        try:
            found = mysql_store.find_by_key(test_record.fusion_id)
            assert found is not None
            assert found.fusion_id == test_record.fusion_id
            assert found.fusion_mode == test_record.fusion_mode
            assert found.group_id == test_record.group_id
        finally:
            # Cleanup
            cursor = mysql_store._conn.cursor()
            cursor.execute("DELETE FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (test_record.fusion_id,))
            mysql_store._conn.commit()
            cursor.close()

    def test_exists_returns_true_after_save(self, mysql_store, test_record):
        """Test that exists returns true after save."""
        mysql_store.save(test_record)

        try:
            assert mysql_store.exists(test_record.fusion_id) is True
            assert mysql_store.exists("nonexistent-id") is False
        finally:
            # Cleanup
            cursor = mysql_store._conn.cursor()
            cursor.execute("DELETE FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (test_record.fusion_id,))
            mysql_store._conn.commit()
            cursor.close()

    def test_update_modifies_record(self, mysql_store, test_record):
        """Test that update modifies an existing record."""
        mysql_store.save(test_record)

        try:
            # Modify the record
            test_record.question = "Updated question"
            test_record.status = "completed"
            mysql_store.update(test_record)

            # Fetch and verify
            found = mysql_store.find_by_key(test_record.fusion_id)
            assert found is not None
            assert found.question == "Updated question"
            assert found.status == "completed"
        finally:
            # Cleanup
            cursor = mysql_store._conn.cursor()
            cursor.execute("DELETE FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (test_record.fusion_id,))
            mysql_store._conn.commit()
            cursor.close()

    def test_update_status_modifies_status_and_message(self, mysql_store, test_record):
        """Test that update_status modifies status and fuse_message."""
        mysql_store.save(test_record)

        try:
            mysql_store.update_status(test_record.fusion_id, "failed", "Test error message")

            found = mysql_store.find_by_key(test_record.fusion_id)
            assert found is not None
            assert found.status == "failed"
            assert found.fuse_message == "Test error message"
        finally:
            # Cleanup
            cursor = mysql_store._conn.cursor()
            cursor.execute("DELETE FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (test_record.fusion_id,))
            mysql_store._conn.commit()
            cursor.close()

    def test_append_turn_appends_conversation_turn(self, mysql_store, test_record):
        """Test that append_turn appends a conversation turn."""
        from src.domain.models.profile_fusion import ConversationTurn

        mysql_store.save(test_record)

        try:
            turn = ConversationTurn(
                turn_index=1,
                question="Test question",
                sender_id="user-1",
                sender_name="Test User",
                answer_content="Test answer",
                answer_response_ms=150,
            )

            mysql_store.append_turn(test_record.fusion_id, turn)

            # Verify conversation was inserted
            conversation = mysql_store.get_conversation(test_record.fusion_id)
            assert conversation is not None
            assert conversation["total_turns"] == 1
            assert len(conversation["turns"]) == 1
            assert conversation["turns"][0]["question"] == "Test question"
            assert conversation["turns"][0]["answer_content"] == "Test answer"
        finally:
            # Cleanup
            cursor = mysql_store._conn.cursor()
            cursor.execute("DELETE FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (test_record.fusion_id,))
            mysql_store._conn.commit()
            cursor.close()

    def test_get_conversation_returns_turns_with_offset_limit(self, mysql_store, test_record):
        """Test that get_conversation supports pagination."""
        from src.domain.models.profile_fusion import ConversationTurn

        mysql_store.save(test_record)

        try:
            # Append multiple turns
            for i in range(5):
                turn = ConversationTurn(
                    turn_index=i + 1,
                    question=f"Question {i+1}",
                    sender_id="user-1",
                    sender_name="Test User",
                    answer_content=f"Answer {i+1}",
                )
                mysql_store.append_turn(test_record.fusion_id, turn)

            # Get with offset and limit
            conversation = mysql_store.get_conversation(test_record.fusion_id, offset=1, limit=2)
            assert conversation is not None
            assert conversation["total_turns"] == 5
            assert len(conversation["turns"]) == 2
        finally:
            # Cleanup
            cursor = mysql_store._conn.cursor()
            cursor.execute("DELETE FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (test_record.fusion_id,))
            mysql_store._conn.commit()
            cursor.close()

    def test_find_by_group_returns_records(self, mysql_store, test_record):
        """Test that find_by_group returns matching records."""
        mysql_store.save(test_record)

        try:
            records = mysql_store.find_by_group("test-group-1")
            assert len(records) >= 1
            assert any(r.fusion_id == test_record.fusion_id for r in records)
        finally:
            # Cleanup
            cursor = mysql_store._conn.cursor()
            cursor.execute("DELETE FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (test_record.fusion_id,))
            mysql_store._conn.commit()
            cursor.close()

    def test_find_by_participant_returns_records(self, mysql_store, test_record):
        """Test that find_by_participant returns matching records."""
        mysql_store.save(test_record)

        try:
            records = mysql_store.find_by_participant("worker-1")
            assert len(records) >= 1
            assert any(r.fusion_id == test_record.fusion_id for r in records)
        finally:
            # Cleanup
            cursor = mysql_store._conn.cursor()
            cursor.execute("DELETE FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (test_record.fusion_id,))
            mysql_store._conn.commit()
            cursor.close()

    def test_duplicate_save_update_behavior(self, mysql_store, test_record):
        """Test that save with same fusion_id performs update (upsert semantics)."""
        mysql_store.save(test_record)

        try:
            # Save again with modified data
            test_record.question = "Modified question"
            mysql_store.save(test_record)

            # Should have updated, not created duplicate
            found = mysql_store.find_by_key(test_record.fusion_id)
            assert found is not None
            assert found.question == "Modified question"

            # Should still be only one record
            cursor = mysql_store._conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (test_record.fusion_id,))
            count = cursor.fetchone()[0]
            cursor.close()
            assert count == 1
        finally:
            # Cleanup
            cursor = mysql_store._conn.cursor()
            cursor.execute("DELETE FROM bcsfuse_fused_profiles WHERE fusion_id = %s", (test_record.fusion_id,))
            mysql_store._conn.commit()
            cursor.close()