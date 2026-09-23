"""Desktop discovery through the public connection service."""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from agentclaw.community.core.engine_runtime.connection import EngineConnectionService
from agentclaw.community.core.engine_runtime.desktop_connection import (
    DesktopConnectionConfig,
)


@pytest.mark.parametrize(
    "engine,path", [("openclaw", "/api/openclaw/ws"), ("hermes", "/api/hermes/ws")]
)
def test_desktop_direct_bypasses_cloud_provider_and_gateway(engine, path):
    bots = Mock()
    bots.get_bot.return_value = {
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "bot_type": "desktop",
        "active_engine": engine,
        "public": "0",
        "id": 100,
    }
    bindings = Mock()
    bindings.get_active_by_bot_and_owner.return_value = SimpleNamespace(id=42)
    desktop = Mock()
    desktop.config = DesktopConnectionConfig()
    desktop.resolve.return_value = SimpleNamespace(
        ws_url=f"ws://localhost:43210{path}",
        target="localhost:43210",
        token="",
        expires_at="2026-09-24T00:00:00+00:00",
    )
    devices = Mock()
    devices.get_device_connection.side_effect = AssertionError(
        "cloud provider must not run"
    )
    service = EngineConnectionService(
        bots, bindings, devices, Mock(), Mock(), Mock(), desktop_connections=desktop
    )
    result = service.build(
        bot_id="bot-1", owner_id="owner-1", caller_id="owner-1", stage="draft"
    )
    assert result.transport_mode == "direct"
    assert result.sockets[0].url == f"ws://localhost:43210{path}"
    desktop.resolve.assert_called_once_with(42, "owner-1", path)
    devices.get_device_connection.assert_not_called()
