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

from secbaas.api.device_manage import ArcaCredentials
from secbaas.core.service.paas import ArcaPaasService


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
        self, arca_credentials, mock_plugin, mock_sandbox
    ):
        """TST-02: When sandbox has no storage, delete_storage is NOT called."""
        # Setup: sandbox info has NO storage attribute
        mock_info = MagicMock()
        # Do NOT set mock_info.storage — so getattr(info, "storage", None) returns None
        mock_sandbox.get_info.return_value = mock_info
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_not_called()
        mock_sandbox.destroy.assert_called_once()

    def test__destroy_device_sync__get_info_fails(
        self, arca_credentials, mock_plugin, mock_sandbox
    ):
        """TST-03: When get_info fails, destroy still succeeds and delete_storage NOT called."""
        # Setup: get_info raises an exception
        mock_sandbox.get_info.side_effect = RuntimeError("SDK connection failed")
        mock_sandbox.destroy.return_value = True

        service = ArcaPaasService(
            credentials=arca_credentials, arca_sandbox_plugin=mock_plugin
        )

        result = service._destroy_device_sync("test-device-id")

        assert result is True
        mock_plugin.delete_storage.assert_not_called()
        mock_sandbox.destroy.assert_called_once()

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
