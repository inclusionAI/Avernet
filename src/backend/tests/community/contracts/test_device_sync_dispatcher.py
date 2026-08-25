"""Rule 25 conformance tests for DeviceSyncDispatcher."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.device_context import UnknownProviderError
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


def test_dispatch_rejects_non_baas_provider() -> None:
    factory = MagicMock()
    dispatcher: DeviceSyncDispatcher = CommunityDeviceSyncDispatcher(factory)
    ctx = SimpleNamespace(bot_id="b1", provider="arca")

    with pytest.raises(
        UnknownProviderError,
        match=r"unsupported provider='arca' \(bot=b1\)",
    ):
        dispatcher.dispatch(ctx)

    factory.assert_not_called()
