"""Desktop contract checks against production policy; no device simulation."""

import pytest
from agentclaw.community.core.engine_runtime.desktop_connection import (
    DesktopConnectionConfig,
    validate_direct_url,
)
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.engine_runtime.gate import require_operable_bot


def test_desktop_has_only_workspace_runtime():
    require_operable_bot("desktop", stage="draft", surface="sessions")
    for stage in ("verify", "online"):
        with pytest.raises(EngineStageNotLiveError):
            require_operable_bot("desktop", stage=stage, surface="sessions")


def test_configuration_is_explicit_and_defaults_to_direct():
    assert DesktopConnectionConfig().mode == "direct"
    assert DesktopConnectionConfig("relay").mode == "relay"
    with pytest.raises(ValueError):
        DesktopConnectionConfig("typo")


def test_direct_address_preserves_real_port_and_engine():
    assert (
        validate_direct_url("ws://localhost:43123/api/hermes/ws", "/api/hermes/ws")
        == "ws://localhost:43123/api/hermes/ws"
    )


@pytest.mark.parametrize(
    "url",
    [
        "wss://example.com/api/hermes/ws",
        "ws://localhost:43123/api/openclaw/ws",
        "ws://user@localhost:43123/api/hermes/ws",
        "ws://localhost:43123/api/hermes/ws#fragment",
    ],
)
def test_direct_rejects_wrong_host_engine_or_credentials(url):
    from agentclaw.community.core.engine_runtime.errors import EngineUpstreamError

    with pytest.raises(EngineUpstreamError):
        validate_direct_url(url, "/api/hermes/ws")
