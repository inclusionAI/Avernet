"""Rule 25 conformance tests for DeviceSyncDispatcher."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugins.community.device_sync_dispatcher import CommunityDeviceSyncDispatcher


def test_dispatch_returns_factory_service_for_context() -> None:
    service = MagicMock(spec=DeviceSync)
    factory = MagicMock(return_value=service)
    dispatcher: DeviceSyncDispatcher = CommunityDeviceSyncDispatcher(factory)
    ctx = SimpleNamespace(bot_id="b1", provider="baas")

    assert dispatcher.dispatch(ctx) is service
    factory.assert_called_once_with(ctx)
