"""Tests for the Singlebox DeviceSync compatibility wrapper."""
from unittest.mock import MagicMock

from agentclaw.community.core.devices.services.singlebox_device_sync import (
    SingleboxDeviceSyncService,
)


def test_singlebox_skips_filter_servers_but_delegates_other_methods():
    delegate = MagicMock()
    delegate.sync_symlinks.return_value = {"success": True}
    delegate.sync_bot_config.return_value = {"success": True}
    delegate.sync_single_mcp.return_value = True
    delegate.sync_remove_mcp.return_value = True
    delegate.has_mcp.return_value = True
    service = SingleboxDeviceSyncService(delegate=delegate)

    assert service.sync_all_mcp_servers([{"server_code": "mcp.example"}]) is True
    assert service.sync_symlinks([]) == {"success": True}
    assert service.sync_bot_config(
        "bot-1", 42, "1", "owner", "user-1", "Alice"
    ) == {"success": True}
    assert service.sync_single_mcp({"server_code": "mcp.example"}) is True
    assert service.sync_remove_mcp("mcp.example") is True
    assert service.has_mcp("mcp.example") is True

    delegate.sync_all_mcp_servers.assert_not_called()
    delegate.sync_symlinks.assert_called_once_with([])
    delegate.sync_bot_config.assert_called_once_with(
        "bot-1", 42, "1", "owner", "user-1", "Alice"
    )
    delegate.sync_single_mcp.assert_called_once_with(
        {"server_code": "mcp.example"},
        api_key=None,
        custom_headers=None,
        endpoint_env="PROD",
        transport_protocol=None,
    )
    delegate.sync_remove_mcp.assert_called_once_with("mcp.example")
    delegate.has_mcp.assert_called_once_with("mcp.example")
