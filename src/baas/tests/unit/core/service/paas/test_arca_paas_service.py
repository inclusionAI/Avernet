"""Tests for ArcaPaasService storage cleanup during destroy_device.

These tests verify the best-effort storage cleanup logic in
_destroy_device_sync(), covering all four edge cases:
- storage present (delete_storage called)
- storage absent (delete_storage NOT called)
- get_info failure (delete_storage NOT called, destroy still succeeds)
- delete_storage failure (destroy still succeeds, warning logged)
"""

import logging
from unittest.mock import MagicMock

import pytest

from secbaas.community.api.device_manage import ArcaCredentials, PaasError
from secbaas.community.core.service.paas import ArcaPaasService
from secbaas.community.spi.sandbox.arca import (
    ArcaSandboxError,
    ArcaSandboxNotFoundError,
)


@pytest.fixture
def mock_sandbox():
    """Create a mock ArcaSandbox (returned by plugin.connect_sync_sandbox)."""
    mock = MagicMock()
    mock.is_ready = True
    return mock


@pytest.fixture
def mock_plugin(mock_sandbox):
    """Create a mock ArcaSandboxPlugin with delete_storage support."""
    mock = MagicMock()
    mock.connect_sync_sandbox.return_value = mock_sandbox
    mock.delete_storage.return_value = True
    return mock


@pytest.fixture
def arca_credentials():
    """Create test Arca credentials with tenant_name."""
    return ArcaCredentials(
        base_url="http://arca.test:8080",
        api_key="test-key",
        timeout=30.0,
        template_id=1,
        template_uuid="tpl-test-001",
        tenant_name="test-tenant",
    )


class TestDestroyDeviceWithStorage:
    """Test destroy_device storage cleanup behavior (TST-01 through TST-04)."""

    @pytest.fixture(autouse=True)
    def _enable_log_propagation(self):
        """BareLoggerPlugin sets propagate=False, which breaks caplog."""
        logger = logging.getLogger("core-service")
        old = logger.propagate
        logger.propagate = True
        yield
        logger.propagate = old

    def test__destroy_device_sync__with_storage(
        self, arca_credentials, mock_plugin, mock_sandbox
    ):
        """TST-01: When sandbox has storage, delete_storage is called with correct args."""
        # Setup: sandbox info has a storage dict
        mock_info = MagicMock()
        mock_info.storage = {"storage_id": "storage-abc"}
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_called_once_with("storage-abc", "test-tenant")
        mock_sandbox.destroy.assert_called_once()

    def test__destroy_device_sync__without_storage(
        self, arca_credentials, mock_plugin, mock_sandbox, caplog
    ):
        """TST-02: When sandbox has no storage, delete_storage is NOT called."""
        # Setup: sandbox info has NO storage attribute
        mock_info = MagicMock()
        del mock_info.storage  # Ensure getattr(info, "storage", None) returns None
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with caplog.at_level(logging.WARNING):
            result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_not_called()
        mock_sandbox.destroy.assert_called_once()
        assert "Storage cleanup skipped: storage attribute missing" in caplog.text
        assert "paas_device_id=test-device-id" in caplog.text

    def test__destroy_device_sync__get_info_fails(
        self, arca_credentials, mock_plugin, mock_sandbox, caplog
    ):
        """TST-03: When get_info fails, destroy still succeeds and delete_storage NOT called."""
        # Setup: get_info raises an exception
        mock_sandbox.get_info.side_effect = RuntimeError("SDK connection failed")
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with caplog.at_level(logging.WARNING):
            result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_not_called()
        mock_sandbox.destroy.assert_called_once()
        assert "Storage cleanup skipped: get_info failed" in caplog.text
        assert "paas_device_id=test-device-id" in caplog.text

    def test__destroy_device_sync__delete_storage_fails(
        self, arca_credentials, mock_plugin, mock_sandbox, caplog
    ):
        """TST-04: When delete_storage returns False, destroy still succeeds and warning logged."""
        # Setup: storage present but delete_storage fails
        mock_info = MagicMock()
        mock_info.storage = {"storage_id": "storage-abc"}
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True
        mock_plugin.delete_storage.return_value = False

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with caplog.at_level(logging.WARNING):
            result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_called_once_with("storage-abc", "test-tenant")
        mock_sandbox.destroy.assert_called_once()
        assert "Storage deletion failed" in caplog.text

    # ── Idempotent destroy tests (from github/dev) ──

    def test__destroy_device_sync__already_missing_is_idempotent(
        self, arca_credentials, mock_plugin
    ):
        """A missing sandbox remains a successful idempotent destroy."""
        mock_plugin.connect_sync_sandbox.side_effect = ArcaSandboxNotFoundError(
            "sandbox not found"
        )
        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        result = service._destroy_device_sync("missing-device-id")

        assert result is True
        assert mock_plugin.connect_sync_sandbox.call_count == 2
        mock_plugin.delete_storage.assert_not_called()

    def test__destroy_device_sync__initial_connect_failure_then_missing_is_idempotent(
        self, arca_credentials, mock_plugin
    ):
        """A transient lookup failure must not break a later not-found destroy."""
        mock_plugin.connect_sync_sandbox.side_effect = [
            RuntimeError("lookup unavailable"),
            ArcaSandboxNotFoundError("sandbox not found"),
        ]
        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        result = service._destroy_device_sync("missing-device-id")

        assert result is True
        assert mock_plugin.connect_sync_sandbox.call_count == 2
        mock_plugin.delete_storage.assert_not_called()

    def test__destroy_device_sync__destroyed_sandbox_connection_is_idempotent(
        self, arca_credentials, mock_plugin, caplog
    ):
        """An ARCA-scheduled sandbox deletion is equivalent to not found."""
        mock_plugin.connect_sync_sandbox.side_effect = ArcaSandboxError(
            "Failed to connect sync sandbox: sandbox destroyed"
        )
        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with caplog.at_level(logging.WARNING):
            result = service._destroy_device_sync("destroyed-device-id")

        assert result is True
        assert mock_plugin.connect_sync_sandbox.call_count == 2
        mock_plugin.delete_storage.assert_not_called()
        assert "treating as successful (already destroyed)" in caplog.text

    def test__destroy_device_sync__connection_failure_is_not_idempotent(
        self, arca_credentials, mock_plugin
    ):
        """A connection failure without an explicit destroyed state must surface."""
        mock_plugin.connect_sync_sandbox.side_effect = ArcaSandboxError(
            "Failed to connect sync sandbox: connection refused"
        )
        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with pytest.raises(PaasError, match="connection refused"):
            service._destroy_device_sync("unreachable-device-id")

    def test__destroy_device_sync__delete_storage_exception_is_best_effort(
        self, arca_credentials, mock_plugin, mock_sandbox, caplog
    ):
        """A storage API exception must not turn a successful destroy into failure."""
        mock_info = MagicMock()
        mock_info.storage = {"storage_id": "storage-abc"}
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True
        mock_plugin.delete_storage.side_effect = RuntimeError("storage unavailable")
        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with caplog.at_level(logging.WARNING):
            result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_called_once_with("storage-abc", "test-tenant")
        assert "Storage deletion exception" in caplog.text

    def test__destroy_device_sync__non_idempotent_arca_error_is_translated(
        self, arca_credentials, mock_plugin, mock_sandbox
    ):
        """A real Arca destroy failure must still surface as a PaasError."""
        mock_sandbox.get_info.return_value = MagicMock(storage=None)
        mock_sandbox.destroy.side_effect = ArcaSandboxError("connection refused")
        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with pytest.raises(PaasError, match="connection refused"):
            service._destroy_device_sync("test-device-id")

    # ── Storage edge-case tests (from phase 01.1) ──

    def test__destroy_device_sync__storage_object_type(
        self, arca_credentials, mock_plugin, mock_sandbox
    ):
        """When sandbox storage is an SDK Storage object, delete_storage is called."""
        mock_info = MagicMock()
        # Simulate SDK Storage object with storage_id attribute
        mock_storage = MagicMock()
        mock_storage.storage_id = "storage-sdk-001"
        mock_info.storage = mock_storage
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_called_once_with(
            "storage-sdk-001", "test-tenant"
        )
        mock_sandbox.destroy.assert_called_once()

    def test__destroy_device_sync__storage_unknown_type(
        self, arca_credentials, mock_plugin, mock_sandbox, caplog
    ):
        """When sandbox storage is unrecognized type, WARNING is logged."""
        mock_info = MagicMock()
        mock_info.storage = "not-a-dict-or-object"
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with caplog.at_level(logging.WARNING):
            result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_not_called()
        mock_sandbox.destroy.assert_called_once()
        assert "Storage cleanup skipped: unrecognized storage type" in caplog.text
        assert "storage_id missing" not in caplog.text
        assert "paas_device_id=test-device-id" in caplog.text

    def test__destroy_device_sync__storage_id_missing(
        self, arca_credentials, mock_plugin, mock_sandbox, caplog
    ):
        """Scenario D: When storage_id is missing, WARNING is logged."""
        mock_info = MagicMock()
        mock_info.storage = {}  # dict but no "storage_id" key
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with caplog.at_level(logging.WARNING):
            result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_not_called()
        mock_sandbox.destroy.assert_called_once()
        assert "Storage cleanup skipped: storage_id missing" in caplog.text
        assert "paas_device_id=test-device-id" in caplog.text

    def test__destroy_device_sync__storage_id_empty_string(
        self, arca_credentials, mock_plugin, mock_sandbox, caplog
    ):
        """When storage_id is an empty string, WARNING is logged and delete_storage NOT called."""
        mock_info = MagicMock()
        mock_info.storage = {"storage_id": ""}
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with caplog.at_level(logging.WARNING):
            result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_not_called()
        mock_sandbox.destroy.assert_called_once()
        assert "Storage cleanup skipped: storage_id is empty string" in caplog.text
        assert "paas_device_id=test-device-id" in caplog.text

    def test__destroy_device_sync__storage_id_non_string(
        self, arca_credentials, mock_plugin, mock_sandbox, caplog
    ):
        """When storage_id is not a string, WARNING is logged and delete_storage NOT called."""
        mock_info = MagicMock()
        mock_info.storage = {"storage_id": 12345}
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with caplog.at_level(logging.WARNING):
            result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_not_called()
        mock_sandbox.destroy.assert_called_once()
        assert "Storage cleanup skipped: storage_id is not a string" in caplog.text
        assert "paas_device_id=test-device-id" in caplog.text

    def test__destroy_device_sync__storage_object_id_none(
        self, arca_credentials, mock_plugin, mock_sandbox, caplog
    ):
        """When Storage object has storage_id=None, WARNING is logged and delete_storage NOT called."""
        mock_info = MagicMock()
        mock_storage = MagicMock()
        mock_storage.storage_id = None
        mock_info.storage = mock_storage
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        with caplog.at_level(logging.WARNING):
            result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_not_called()
        mock_sandbox.destroy.assert_called_once()
        assert "Storage cleanup skipped: storage_id missing" in caplog.text
        assert "paas_device_id=test-device-id" in caplog.text


def test__safe_repr__truncates():
    """_safe_repr truncates output to max_len with ellipsis indicator."""
    from secbaas.community.core.service.paas._arca_paas_service import _safe_repr

    result = _safe_repr("x" * 5000, max_len=10)
    assert len(result) == 10
    # Truncated at max_len with "..." appended as truncation indicator
    assert result.endswith("...")
    assert result.startswith("'")


def test__safe_repr__repr_fails():
    """_safe_repr returns error marker when repr() raises."""
    from secbaas.community.core.service.paas._arca_paas_service import _safe_repr

    class BadRepr:
        def __repr__(self):
            raise RuntimeError("boom")

    result = _safe_repr(BadRepr())
    assert "<repr failed:" in result
    assert "boom" in result
