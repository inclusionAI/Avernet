"""Mock-backed transport contract tests; these do not assert real device reachability."""

from unittest.mock import AsyncMock, Mock

import pytest
from agentclaw.community.core.bot_inventory.local_progress import LocalProgressService
from agentclaw.community.core.engine_runtime.desktop_connection import (
    DesktopConnectionConfig,
    DesktopConnectionService,
)
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.resources.link_workflow import LinkWorkflow
from agentclaw.community.core.resources.models import Resource, ResourceType
from agentclaw.community.core.resources.service import ResourceNotFoundError
from agentclaw.community.core.service_bot.services.baas_service import (
    BotWsConnectionInfoResponse,
)


def test_direct_requests_real_ws_info_with_owner_engine_and_mode():
    provider = Mock()
    provider.get_ws_info.return_value = BotWsConnectionInfoResponse(
        ws_url="ws://localhost:42311/api/hermes/ws",
        token="",
        target="localhost:42311",
        expires_at="2026-09-24T00:00:00+00:00",
    )
    service = DesktopConnectionService(provider, DesktopConnectionConfig())
    result = service.resolve(37, "owner", "/api/hermes/ws")
    assert result.ws_url == "ws://localhost:42311/api/hermes/ws"
    provider.get_ws_info.assert_called_once_with(
        bind_id=37,
        device_affinity="owner",
        path="/api/hermes/ws",
        ws_conn_mode="direct",
    )


def test_start_progress_uses_authorized_bot_runtime_not_request_uuid():
    workflow, provider = Mock(), Mock()
    workflow.get_bot.return_value = {"device_id": "runtime-uuid"}
    provider.get_bot_start_progress.return_value = {
        "status": "downloading",
        "progress": "23",
    }
    result = LocalProgressService(workflow, provider).get(
        bot_id="logical-id", owner_id="owner", header_space_id=None
    )
    assert result["progress"] == "23"
    provider.get_bot_start_progress.assert_called_once_with(
        bot_uuid="runtime-uuid", device_affinity="owner"
    )
    workflow.get_bot.assert_called_once_with(
        bot_id="logical-id", owner_id="owner", header_space_id=None
    )


def test_progress_denial_does_not_reach_baas():
    workflow, provider = Mock(), Mock()
    workflow.get_bot.side_effect = NotFound("Bot not found")
    with pytest.raises(NotFound):
        LocalProgressService(workflow, provider).get(
            bot_id="b", owner_id="x", header_space_id=None
        )
    provider.get_bot_start_progress.assert_not_called()


@pytest.mark.asyncio
async def test_link_with_same_bot_id_but_another_owner_is_not_mutable():
    factory, repo, resolver, passport = Mock(), Mock(), Mock(), Mock()
    factory.create.return_value.get_resource.return_value = Resource(
        id="r",
        name="link",
        resource_type=ResourceType.LINK,
        bolt_id="default",
        user_id="other",
    )
    factory.create.return_value.delete_resource = AsyncMock()
    service = LinkWorkflow(factory, repo, resolver, passport)
    with pytest.raises(ResourceNotFoundError):
        await service.delete("default", "owner", "r")
    factory.create.return_value.delete_resource.assert_not_called()


def test_relay_discovery_preserves_owner_affinity_and_mode():
    provider = Mock()
    service = DesktopConnectionService(provider, DesktopConnectionConfig(mode="relay"))
    assert (
        service.resolve(42, "owner", "/api/openclaw/ws")
        is provider.get_ws_info.return_value
    )
    provider.get_ws_info.assert_called_once_with(
        bind_id=42,
        device_affinity="owner",
        path="/api/openclaw/ws",
        ws_conn_mode="relay",
    )


@pytest.mark.parametrize("failure", [False, RuntimeError("upstream failed")])
def test_new_link_sync_propagates_failure_without_changing_legacy_default(failure):
    from agentclaw.community.core.resources.dependencies.service_dep import (
        sync_yuque_permissions,
    )

    repo, passport = Mock(), Mock()
    repo.list_resources.return_value = []
    if isinstance(failure, Exception):
        passport.save_sub_resources.side_effect = failure
    else:
        passport.save_sub_resources.return_value = failure
    with pytest.raises(RuntimeError):
        sync_yuque_permissions("b", "u", repo, passport, strict=True)
    sync_yuque_permissions("b", "u", repo, passport)
    assert passport.save_sub_resources.call_count == 2


@pytest.mark.asyncio
async def test_duplicate_link_batch_does_not_write():
    factory, repo, resolver, passport = Mock(), Mock(), Mock(), Mock()
    service = LinkWorkflow(factory, repo, resolver, passport)
    link = {"url": "https://example.com/doc", "link_type": "antcode"}
    with pytest.raises(ValueError):
        await service.create("b", "u", [link, link])
    factory.create.return_value.create_link_resource.assert_not_called()
    passport.save_sub_resources.assert_not_called()


@pytest.mark.parametrize(
    "config,expected", [({}, "direct"), ({"mode": "relay"}, "relay")]
)
def test_composition_root_resolves_desktop_contract(monkeypatch, config, expected):
    from injector import Injector, InstanceProvider, Module
    from agentclaw.community.api.baas_service import BaasServiceProtocol
    from agentclaw.community.core.engine_runtime.desktop_connection import (
        DesktopConnectionServiceProtocol,
    )
    from agentclaw.community.di.modules import engine_runtime_module

    provider = Mock()

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(BaasServiceProtocol, to=InstanceProvider(provider))

    monkeypatch.setattr(
        engine_runtime_module,
        "read_user_config",
        lambda: {"desktop_connection": config},
    )
    service = Injector([Bindings(), engine_runtime_module.EngineRuntimeModule()]).get(
        DesktopConnectionServiceProtocol
    )
    assert service.config.mode == expected


def test_unknown_desktop_configuration_fails_in_composition_root(monkeypatch):
    from agentclaw.community.di.modules import engine_runtime_module

    monkeypatch.setattr(
        engine_runtime_module,
        "read_user_config",
        lambda: {"desktop_connection": {"typo": True}},
    )
    with pytest.raises(ValueError, match="unknown"):
        engine_runtime_module.EngineRuntimeModule().desktop_connections(Mock())
