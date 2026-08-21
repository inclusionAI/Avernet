"""Tests for the selection-only Local DeviceSync dispatcher."""

from types import SimpleNamespace
from unittest.mock import Mock

from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.plugins.local.device_sync_dispatcher import (
    LocalDeviceSyncDispatcher,
)


def test_dispatch_returns_service_created_by_injected_factory():
    service = Mock(spec=DeviceSync)
    factory = Mock(return_value=service)
    dispatcher = LocalDeviceSyncDispatcher(device_sync_factory=factory)

    returned = dispatcher.dispatch(
        SimpleNamespace(bot_id="bot-1", provider="local", bot_type="personal")
    )

    assert returned is service
    factory.assert_called_once()
    assert factory.call_args.args[0].provider == "local"
