"""
Storage Observability Contract Tests

Tests for logging and redaction in storage providers.

S30A: Observability logging contract tests.
"""

import logging
import re
import pytest
from unittest.mock import Mock, patch, MagicMock
from io import StringIO

from src.infra.public.observability.storage_logging import (
    mask_value,
    mask_host,
    mask_user,
    mask_url,
    sanitize_config,
    sanitize_key_fields,
    generate_correlation_id,
    log_storage_event,
    log_storage_error,
    set_correlation_id,
    get_correlation_id,
)


class TestSecretRedaction:
    """Test secret redaction functions."""

    def test_mask_value_masks_bearer_token(self):
        """Test that Bearer tokens are masked."""
        value = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        masked = mask_value(value)
        assert masked == "Bearer ****"
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in masked

    def test_mask_value_masks_mysql_dsn(self):
        """Test that MySQL DSN passwords are masked."""
        value = "mysql://admin:secretpassword@localhost:3306/database"
        masked = mask_value(value)
        assert masked == "mysql://admin:****@localhost:3306/database"
        assert "secretpassword" not in masked

    def test_mask_value_masks_postgres_dsn(self):
        """Test that PostgreSQL DSN passwords are masked."""
        value = "postgres://user:pass123@db.example.com:5432/mydb"
        masked = mask_value(value)
        assert masked == "postgres://user:****@db.example.com:5432/mydb"
        assert "pass123" not in masked

    def test_mask_value_masks_redis_dsn(self):
        """Test that Redis DSN passwords are masked."""
        value = "redis://default:redispass@localhost:6379/0"
        masked = mask_value(value)
        assert masked == "redis://default:****@localhost:6379/0"
        assert "redispass" not in masked

    def test_mask_value_preserves_safe_values(self):
        """Test that safe values are preserved."""
        value = "http://example.com/api/v1/resource"
        masked = mask_value(value)
        assert masked == value

    def test_mask_value_handles_none(self):
        """Test that None is handled."""
        masked = mask_value(None)
        assert masked == ""

    def test_mask_host_masks_long_host(self):
        """Test that long hostnames are masked."""
        host = "database.example.com"
        masked = mask_host(host)
        assert masked == "dat****"
        assert "example.com" not in masked

    def test_mask_host_masks_short_host(self):
        """Test that short hostnames are fully masked."""
        host = "db"
        masked = mask_host(host)
        assert masked == "****"

    def test_mask_host_handles_none(self):
        """Test that None is handled."""
        masked = mask_host(None)
        assert masked == ""

    def test_mask_user_masks_long_user(self):
        """Test that long usernames are masked."""
        user = "administrator"
        masked = mask_user(user)
        assert masked == "ad****"
        assert "ministrator" not in masked

    def test_mask_user_masks_short_user(self):
        """Test that short usernames are fully masked."""
        user = "ab"
        masked = mask_user(user)
        assert masked == "****"

    def test_mask_user_handles_none(self):
        """Test that None is handled."""
        masked = mask_user(None)
        assert masked == ""

    def test_mask_url_masks_password(self):
        """Test that URL passwords are masked."""
        url = "mysql://admin:secret123@localhost:3306/db"
        masked = mask_url(url)
        assert masked == "mysql://admin:****@localhost:3306/db"
        assert "secret123" not in masked

    def test_mask_url_preserves_no_password(self):
        """Test that URLs without passwords are preserved."""
        url = "http://example.com/path"
        masked = mask_url(url)
        assert masked == url

    def test_mask_url_handles_none(self):
        """Test that None is handled."""
        masked = mask_url(None)
        assert masked == ""

    def test_sanitize_config_masks_secrets(self):
        """Test that sanitize_config masks secret fields."""
        config = {
            "host": "localhost",
            "port": 3306,
            "user": "admin",
            "password": "secret123",
            "api_key": "ak_1234567890abcdef",
            "auth_token": "tok_xyz987",
        }
        sanitized = sanitize_config(config)

        assert sanitized["host"] == "localhost"
        assert sanitized["port"] == 3306
        assert sanitized["user"] == "admin"
        assert sanitized["password"] == "***MASKED***"
        assert sanitized["api_key"] == "***MASKED***"
        assert sanitized["auth_token"] == "***MASKED***"

    def test_sanitize_config_handles_nested_dicts(self):
        """Test that sanitize_config handles nested dictionaries."""
        config = {
            "database": {
                "host": "localhost",
                "password": "secret",
            },
            "cache": {
                "url": "redis://localhost",
                "token": "cache_token",
            },
        }
        sanitized = sanitize_config(config)

        assert sanitized["database"]["host"] == "localhost"
        assert sanitized["database"]["password"] == "***MASKED***"
        assert sanitized["cache"]["url"] == "redis://localhost"
        assert sanitized["cache"]["token"] == "***MASKED***"

    def test_sanitize_key_fields_masks_ids(self):
        """Test that sanitize_key_fields masks ID values."""
        key_fields = {
            "worker_id": "worker_abc123def456",
            "profile_key": "profile_xyz789uvw012",
            "binding_id": "binding_123456",
        }
        sanitized = sanitize_key_fields(key_fields)

        assert "wo****" in sanitized["worker_id"]
        assert "pr****" in sanitized["profile_key"]
        assert "bi****" in sanitized["binding_id"]

    def test_sanitize_key_fields_handles_short_ids(self):
        """Test that short IDs are fully masked."""
        key_fields = {
            "id": "ab",
        }
        sanitized = sanitize_key_fields(key_fields)

        assert sanitized["id"] == "****"


class TestCorrelationId:
    """Test correlation ID functions."""

    def test_generate_correlation_id_format(self):
        """Test that correlation ID has correct format."""
        corr_id = generate_correlation_id()

        # Format: val_{timestamp}_{random_8char}
        pattern = r"val_\d+_[a-f0-9]{8}"
        assert re.match(pattern, corr_id), f"Correlation ID {corr_id} doesn't match expected format"

    def test_set_and_get_correlation_id(self):
        """Test that correlation ID can be set and retrieved."""
        test_id = "test_correlation_123"
        set_correlation_id(test_id)
        retrieved = get_correlation_id()

        assert retrieved == test_id

    def test_get_correlation_id_generates_if_missing(self):
        """Test that get_correlation_id generates ID if not set."""
        # Clear any existing correlation ID
        set_correlation_id("")

        corr_id = get_correlation_id()

        # Should generate a new one
        assert corr_id.startswith("val_")
        assert len(corr_id) > 10


class TestLoggingFunctions:
    """Test log_storage_event and log_storage_error functions."""

    def test_log_storage_event_logs_required_fields(self):
        """Test that log_storage_event logs all required fields."""
        logger = logging.getLogger("test_logger")
        logger.setLevel(logging.INFO)

        # Capture log output
        with patch.object(logger, 'log') as mock_log:
            log_storage_event(
                logger,
                logging.INFO,
                "test_event",
                component="test_component",
                operation="test_operation",
                validation_phase="test_phase",
                backend="mysql",
                target_resource="test_table",
                duration_ms=123.45,
                result="success",
                retry_count=0,
                extra_field="extra_value",
            )

            # Verify log was called
            assert mock_log.called

            # Get the extra dict from the call
            call_args = mock_log.call_args
            extra = call_args[1]['extra']

            # Verify required fields
            assert extra['component'] == "test_component"
            assert extra['operation'] == "test_operation"
            assert extra['validation_phase'] == "test_phase"
            assert 'correlation_id' in extra
            assert extra['backend'] == "mysql"
            assert extra['target_resource'] == "test_table"
            assert extra['result'] == "success"
            assert extra['retry_count'] == 0
            assert extra['duration_ms'] == 123.45
            assert extra['extra_field'] == "extra_value"

    def test_log_storage_error_logs_error_fields(self):
        """Test that log_storage_error logs error fields."""
        logger = logging.getLogger("test_logger")
        logger.setLevel(logging.ERROR)

        test_error = ValueError("test error message")
        test_error.code = "TEST_ERROR"

        with patch.object(logger, 'error') as mock_error:
            log_storage_error(
                logger,
                "test_error_event",
                component="test_component",
                operation="test_operation",
                validation_phase="test_phase",
                backend="qdrant",
                target_resource="test_collection",
                error=test_error,
                duration_ms=456.78,
                retry_count=2,
                extra_field="extra_value",
            )

            # Verify error was logged
            assert mock_error.called

            # Get the extra dict from the call
            call_args = mock_error.call_args
            extra = call_args[1]['extra']

            # Verify error fields
            assert extra['error_class'] == "ValueError"
            assert extra['error_code'] == "TEST_ERROR"
            assert extra['result'] == "failure"
            assert extra['retry_count'] == 2
            assert extra['duration_ms'] == 456.78
            assert extra['extra_field'] == "extra_value"

            # Verify exc_info=True
            assert call_args[1]['exc_info'] is True

    def test_log_storage_event_masks_secrets(self):
        """Test that log_storage_event masks secrets in extra fields."""
        logger = logging.getLogger("test_logger")
        logger.setLevel(logging.INFO)

        with patch.object(logger, 'log') as mock_log:
            log_storage_event(
                logger,
                logging.INFO,
                "test_event",
                component="test_component",
                operation="test_operation",
                validation_phase="test_phase",
                backend="mysql",
                target_resource="test_table",
                password="secret_password",
                api_key="ak_123456",
                token="tok_xyz",
            )

            # Get the extra dict from the call
            call_args = mock_log.call_args
            extra = call_args[1]['extra']

            # Verify secrets are masked
            assert extra['password'] == "***MASKED***"
            assert extra['api_key'] == "***MASKED***"
            assert extra['token'] == "***MASKED***"


class TestNoInternalDependencies:
    """Test that observability utilities don't import internal dependencies."""

    def test_no_bcsfuse_internal_import(self):
        """Test that storage_logging doesn't import bcsfuse_internal."""
        from src.infra.public.observability import storage_logging

        module_file = storage_logging.__file__
        with open(module_file, 'r') as f:
            content = f.read()

        # Verify no internal imports
        forbidden_imports = [
            'bcsfuse_internal',
            'sofapy',
            'sofapy_base',
            'ant_sofapy_base',
            'mist',
            'mist_client',
            'layotto',
            'src.infra.config.zdas_settings',
            'src.infra.adapters.zdas_',
        ]

        for forbidden in forbidden_imports:
            assert forbidden not in content, f"Found forbidden import: {forbidden}"

    def test_no_forbidden_imports_in_utility_module(self):
        """Test that bootstrap.oss_diagnostics doesn't have forbidden imports."""
        from src.bootstrap import oss_diagnostics

        module_file = oss_diagnostics.__file__
        with open(module_file, 'r') as f:
            content = f.read()

        # Verify no internal imports
        forbidden_imports = [
            'bcsfuse_internal',
            'sofapy',
            'layotto',
            'mist',
        ]

        for forbidden in forbidden_imports:
            assert forbidden not in content, f"Found forbidden import: {forbidden}"


class TestLoggingWithoutRealResources:
    """Test logging without requiring real MySQL or Qdrant."""

    def test_mysql_connection_failure_logging_masks_password(self, caplog):
        """Test that MySQL connection failure logs don't expose password."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        with caplog.at_level(logging.ERROR):
            # Try to connect to non-existent MySQL with password
            store = MySQLWorkerProfileBindingStore(
                host="nonexistent_host",
                port=3306,
                user="test_user",
                password="super_secret_password_123",
                database="test_db",
            )

            # Try to trigger connection (should fail)
            try:
                store._ensure_connection()
            except RuntimeError:
                pass

            # Check logs don't contain password
            log_messages = [record.message for record in caplog.records]
            for message in log_messages:
                assert "super_secret_password_123" not in message

            # Check extra fields
            for record in caplog.records:
                if hasattr(record, 'password'):
                    assert record.password == "***MASKED***" or record.password != "super_secret_password_123"

    def test_qdrant_init_failure_logging_masks_secrets(self, caplog):
        """Test that Qdrant initialization logs don't expose secrets."""
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )

        with caplog.at_level(logging.ERROR):
            # Create Qdrant store (should work in local mode)
            store = QdrantLocalVectorStore(
                collection_name="test_collection",
                path="/tmp/test_qdrant",
                dimension=1024,
                distance="Cosine",
            )

            # Verify no secrets in logs
            log_messages = [record.message for record in caplog.records]
            for message in log_messages:
                # Should not contain API keys or tokens
                assert "api_key" not in message.lower() or "***MASKED***" in message
                assert "token" not in message.lower() or "***MASKED***" in message
                assert "secret" not in message.lower() or "***MASKED***" in message


class TestRequiredFieldsPresence:
    """Test that all required fields are present in logs."""

    def test_mysql_logs_include_all_required_fields(self, caplog):
        """Test that MySQL logs include all required fields from policy."""
        from src.infra.public.stores.mysql_worker_profile_binding_store import (
            MySQLWorkerProfileBindingStore,
        )

        required_fields = [
            'component',
            'operation',
            'validation_phase',
            'correlation_id',
            'backend',
            'target_resource',
            # Note: duration_ms and result are only on success/failure logs
        ]

        with caplog.at_level(logging.DEBUG):
            # Try to connect
            store = MySQLWorkerProfileBindingStore(
                host="localhost",
                port=3306,
                user="test_user",
                password="test_password",
                database="test_db",
            )

            try:
                store._ensure_connection()
            except RuntimeError:
                pass

            # Check that logs have required fields
            for record in caplog.records:
                if hasattr(record, 'component'):
                    for field in required_fields:
                        assert hasattr(record, field), f"Missing required field: {field}"

    def test_qdrant_logs_include_all_required_fields(self, caplog):
        """Test that Qdrant logs include all required fields from policy."""
        from src.infra.public.vectorstores.qdrant_local_vector_store import (
            QdrantLocalVectorStore,
        )

        required_fields = [
            'component',
            'operation',
            'validation_phase',
            'correlation_id',
            'backend',
            'target_resource',
        ]

        with caplog.at_level(logging.DEBUG):
            # Create Qdrant store
            store = QdrantLocalVectorStore(
                collection_name="test_collection",
                path="/tmp/test_qdrant_logs",
                dimension=512,
                distance="Cosine",
            )

            # Trigger client initialization
            try:
                store._ensure_client()
            except Exception:
                pass

            # Check that logs have required fields
            for record in caplog.records:
                if hasattr(record, 'component'):
                    for field in required_fields:
                        assert hasattr(record, field), f"Missing required field: {field}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])