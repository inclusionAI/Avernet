from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from agentclaw.community.core.bot_management.expert_readiness import expert_adapter_ready
from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterEndpointNotFoundError
from agentclaw.community.plugins.local.device_adapter_transport import InMemoryDeviceAdapterTransport


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
    transport.invoke.assert_awaited_once_with(
        {"bind_id": 42, "probe_runtime_readiness": True}, "GET", "/readiness", timeout=3.0,
    )


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


def test_singlebox_resolves_the_actual_baas_binding_before_probe(monkeypatch):
    bindings = Mock()
    bindings.get_by_id.side_effect = lambda bid: SimpleNamespace(
        id=bid, device_id=f"device-{bid}", device_provider="baas"
    )
    bots = Mock()
    bots.get_by_binding_id.return_value = {"bot_type": "personal"}
    builders = [Mock() for _ in range(4)]
    builders[1].build.side_effect = lambda binding, owner, device_uuid=None: {
        "bind_id": binding.id, "target": f"127.0.0.1:{20000 + binding.id}"
    }
    resolver = DeviceContextResolver(
        binding_repository=bindings, bot_repository=bots,
        arca_builder=builders[0], baas_builder=builders[1],
        teclaw_builder=builders[2], local_builder=builders[3],
    )
    requests = []
    original_client = httpx.AsyncClient

    def client(**kwargs):
        def handle(request):
            requests.append(str(request.url))
            return httpx.Response(200, json={"state": "ready"})
        return original_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    transport = InMemoryDeviceAdapterTransport(default_adapter_url="http://shared:9999")
    for binding_id in (1, 2):
        assert expert_adapter_ready(
            bot={"binding_id": binding_id}, bot_id=f"bot-{binding_id}", owner_id="owner",
            resolver=resolver, transport=transport, concrete_connection=True,
        )
    assert requests == ["http://127.0.0.1:20001/readiness", "http://127.0.0.1:20002/readiness"]


@pytest.mark.parametrize("is_local", [True, False])
def test_creation_composition_uses_transport_appropriate_resolution(is_local):
    from agentclaw.community.di.modules.bot_management_module import BotManagementModule
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport

    resolver = Mock()
    resolver.resolve_for_binding.return_value = SimpleNamespace(conn_info={"target": "localhost:20001"})
    resolver.resolve_for_binding_invoke.return_value = SimpleNamespace(conn_info={"bind_id": 42})
    transport = InMemoryDeviceAdapterTransport() if is_local else SimpleNamespace()
    transport.invoke = AsyncMock(return_value={"state": "ready"})
    injector = Mock()
    injector.get.side_effect = lambda key: transport if key is DeviceAdapterTransport else resolver
    handler = BotManagementModule().bot_create_with_manifest_handler(
        injector=injector, passport_plugin=Mock(), auth_rel_plugin=Mock(), bot_repository=Mock(),
    )
    assert handler._creation_readiness_probe(
        spec={"expert_origin": {"expert_id": "expert"}}, bot={"binding_id": 42},
        bot_id="bot", owner_id="owner",
    )
    chosen = resolver.resolve_for_binding if is_local else resolver.resolve_for_binding_invoke
    chosen.assert_called_once_with(42, "owner", bot_id="bot")
