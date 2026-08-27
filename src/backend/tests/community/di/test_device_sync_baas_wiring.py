from types import SimpleNamespace
from typing import Annotated
from unittest.mock import MagicMock

from agentclaw.community.core.config_compose.services.config_composer import ConfigComposer
from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService
from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport
from agentclaw.community.core.devices.services.singlebox_device_sync import SingleboxDeviceSyncService
from agentclaw.community.core.devices.services.teclaw_device_sync import TeclawDeviceSyncService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
from agentclaw.community.di.modules.infrastructure.community.device_sync import CommunityDeviceSyncModule
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_GENERAL
from agentclaw.community.plugins.community.device_sync_dispatcher import CommunityDeviceSyncDispatcher


def _ctx(provider="baas"):
    return SimpleNamespace(
        provider=provider, bot_id="b", user_id="u1", bot_type="personal",
        conn_info={"bind_id": 1, "engine_port": 20003, "tenant": "", "paas_device_id": "u"},
    )


def _bot_row(**overrides):
    row = {
        "bot_id": "b",
        "bot_name": "助手",
        "owner_id": "u1",
        "entity_id": "staff_u1",
        "entity_type": "staff",
        "active_engine": "teclaw",
    }
    row.update(overrides)
    return row


def _injector(bot_row: dict | None = None):
    """Fake ``Injector`` whose ``get`` serves the teclaw branch's lazy lookups."""
    bot_repo = MagicMock()
    bot_repo.get_by_id.return_value = _bot_row() if bot_row is None else bot_row
    served = {
        BotRepository: bot_repo,
        ConfigComposer: MagicMock(),
        BotPublishService: MagicMock(),
        Annotated[HttpClient, QUALIFIER_GENERAL]: MagicMock(),
    }
    injector = MagicMock()
    injector.get.side_effect = lambda key: served[key]
    return injector


def test_community_dispatcher_factory_builds_shared_baas_service():
    dispatcher = CommunityDeviceSyncModule().device_sync_dispatcher(MagicMock(), _injector())
    service = dispatcher.dispatch(_ctx())
    assert isinstance(dispatcher, CommunityDeviceSyncDispatcher)
    assert isinstance(service, BaasDeviceSyncService)
    assert isinstance(service._transport, BaasInvokeTransport)


def test_singlebox_dispatcher_factory_wraps_shared_baas_service():
    dispatcher = CommunityDeviceSyncModule(
        device_sync_wrapper=SingleboxDeviceSyncService,
    ).device_sync_dispatcher(MagicMock(), _injector())
    service = dispatcher.dispatch(_ctx())
    assert isinstance(dispatcher, CommunityDeviceSyncDispatcher)
    assert isinstance(service, SingleboxDeviceSyncService)
    assert isinstance(service._delegate, BaasDeviceSyncService)
    assert isinstance(service._delegate._transport, BaasInvokeTransport)


# ── teclaw branch ────────────────────────────────────────────────────────


def test_teclaw_provider_builds_the_whole_artifact_service():
    dispatcher = CommunityDeviceSyncModule().device_sync_dispatcher(MagicMock(), _injector())

    service = dispatcher.dispatch(_ctx(provider="teclaw"))

    assert isinstance(service, TeclawDeviceSyncService)


def test_teclaw_service_takes_identity_from_the_bot_row():
    """``entity_id`` (compose scope) and ``owner_id`` (binding lookup) come off
    the ``ac_bots`` row and stay distinct."""
    dispatcher = CommunityDeviceSyncModule().device_sync_dispatcher(MagicMock(), _injector())

    service = dispatcher.dispatch(_ctx(provider="teclaw"))

    assert service._entity_id == "staff_u1"
    assert service._owner_id == "u1"
    assert service._user_id == "u1"
    assert service._bot_name == "助手"
    assert service._entity_type == "staff"
    assert service._engine_type == "teclaw"


def test_teclaw_service_falls_back_to_ctx_identity_when_the_bot_row_is_missing():
    dispatcher = CommunityDeviceSyncModule().device_sync_dispatcher(MagicMock(), _injector(bot_row={}))

    service = dispatcher.dispatch(_ctx(provider="teclaw"))

    assert service._owner_id == "u1"
    assert service._entity_id == "u1"
    assert service._engine_type == "teclaw"
    assert service._entity_type == "staff"


def test_singlebox_wrapper_is_not_applied_to_the_teclaw_branch():
    """The wrapper defers ``sync_all_mcp_servers`` for the per-domain BaaS push;
    teclaw never issues that call, so wrapping would drop a real delivery."""
    dispatcher = CommunityDeviceSyncModule(
        device_sync_wrapper=SingleboxDeviceSyncService,
    ).device_sync_dispatcher(MagicMock(), _injector())

    service = dispatcher.dispatch(_ctx(provider="teclaw"))

    assert isinstance(service, TeclawDeviceSyncService)


# ── real DI graph ────────────────────────────────────────────────────────


def test_teclaw_branch_resolves_through_the_real_injector(test_injector):
    """The teclaw branch pulls ``ConfigComposer`` / ``BotPublishService`` /
    ``HttpClient[general]`` out of the injector at dispatch time; a key that is
    not bound in a profile would only surface here, not in the mock wiring
    tests above."""
    from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher

    service = test_injector.get(DeviceSyncDispatcher).dispatch(_ctx(provider="teclaw"))

    assert isinstance(service, TeclawDeviceSyncService)
    assert isinstance(service._composer_provider(), ConfigComposer)
    assert isinstance(service._draft_recorder(), BotPublishService)
    assert service._http_client is not None


def test_bot_row_lookup_failure_degrades_instead_of_escaping_dispatch(test_injector):
    """``dispatch`` is a construction seam whose callers only expect
    ``DeviceSyncUnavailableError``; a repo error must not escape it."""
    from unittest.mock import patch

    from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher

    dispatcher = test_injector.get(DeviceSyncDispatcher)
    with patch.object(
        type(test_injector.get(BotRepository)),
        "get_by_id",
        side_effect=RuntimeError("db down"),
    ):
        service = dispatcher.dispatch(_ctx(provider="teclaw"))

    assert isinstance(service, TeclawDeviceSyncService)
    assert service._owner_id == "u1"
