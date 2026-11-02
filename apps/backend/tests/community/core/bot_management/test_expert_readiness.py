from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.bot_management.expert_readiness import expert_adapter_ready
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterEndpointNotFoundError


@pytest.mark.parametrize("state,expected", [("ready", True), ("starting", False), ("failed", False)])
def test_expert_readiness_uses_current_binding_and_adapter_state(state, expected):
    resolver = Mock()
    resolver.resolve_for_binding_invoke.return_value = SimpleNamespace(conn_info={"bind_id": 42})
    transport = SimpleNamespace(invoke=AsyncMock(return_value={"state": state}))
    assert expert_adapter_ready(
        bot={"binding_id": 42}, bot_id="expert-bot", owner_id="owner",
        resolver=resolver, transport=transport,
    ) is expected
    resolver.resolve_for_binding_invoke.assert_called_once_with(42, "owner", bot_id="expert-bot")
    transport.invoke.assert_awaited_once_with({"bind_id": 42}, "GET", "/readiness", timeout=3.0)


def test_missing_binding_does_not_probe_another_instance():
    resolver = Mock()
    transport = SimpleNamespace(invoke=AsyncMock())
    assert not expert_adapter_ready(
        bot={}, bot_id="expert-bot", owner_id="owner", resolver=resolver, transport=transport,
    )
    resolver.resolve_for_binding_invoke.assert_not_called()


def test_legacy_adapter_requires_a_successful_health_response():
    resolver = Mock()
    resolver.resolve_for_binding_invoke.return_value = SimpleNamespace(conn_info={"bind_id": 42})
    transport = SimpleNamespace(invoke=AsyncMock(side_effect=[
        DeviceAdapterEndpointNotFoundError('{"detail":"Not Found"}', standard_route_missing=True),
        {"status": "ok"},
    ]))
    assert expert_adapter_ready(
        bot={"binding_id": 42}, bot_id="expert-bot", owner_id="owner",
        resolver=resolver, transport=transport,
    )
    assert transport.invoke.await_args_list[1].args[2] == "/health"


def test_proxy_404_is_not_treated_as_a_legacy_adapter():
    resolver = Mock()
    resolver.resolve_for_binding_invoke.return_value = SimpleNamespace(conn_info={"bind_id": 42})
    transport = SimpleNamespace(invoke=AsyncMock(side_effect=DeviceAdapterEndpointNotFoundError("missing device")))
    with pytest.raises(DeviceAdapterEndpointNotFoundError):
        expert_adapter_ready(
            bot={"binding_id": 42}, bot_id="expert-bot", owner_id="owner",
            resolver=resolver, transport=transport,
        )
    assert transport.invoke.await_count == 1
