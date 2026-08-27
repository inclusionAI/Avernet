"""DeviceSync DI wiring: per-provider factory components + dispatcher routing.

Each provider owns its own construction module; ``CommunityDeviceSyncModule``
installs both and only routes. These tests exercise the two factories in
isolation, then the routing on top, then the whole thing through the real
injector.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.core.config_compose.services.config_composer import ConfigComposer
from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService
from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport
from agentclaw.community.core.devices.services.singlebox_device_sync import SingleboxDeviceSyncService
from agentclaw.community.core.devices.services.teclaw_device_sync import TeclawDeviceSyncService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
from agentclaw.community.di.modules.infrastructure.community.baas_device_sync import (
    BaasDeviceSyncFactory,
    BaasDeviceSyncModule,
)
from agentclaw.community.di.modules.infrastructure.community.device_sync import CommunityDeviceSyncModule
from agentclaw.community.di.modules.infrastructure.community.teclaw_device_sync import (
    TeclawDeviceSyncFactory,
    TeclawDeviceSyncModule,
)
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
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


def _teclaw_factory(bot_row: dict | None = None) -> TeclawDeviceSyncFactory:
    bot_repo = MagicMock()
    bot_repo.get_by_id.return_value = _bot_row() if bot_row is None else bot_row
    return TeclawDeviceSyncModule().teclaw_device_sync_factory(
        baas_service=MagicMock(),
        bot_repo=bot_repo,
        http_client=MagicMock(),
        composer=MagicMock(),
        draft_recorder=lambda: MagicMock(),
    )


# ── baas factory component ───────────────────────────────────────────────


def test_baas_factory_builds_the_shared_service_over_the_invoke_transport():
    factory = BaasDeviceSyncModule().baas_device_sync_factory(MagicMock())

    service = factory(_ctx())

    assert isinstance(service, BaasDeviceSyncService)
    assert isinstance(service._transport, BaasInvokeTransport)


def test_baas_factory_applies_the_singlebox_wrapper():
    factory = BaasDeviceSyncModule(SingleboxDeviceSyncService).baas_device_sync_factory(
        MagicMock()
    )

    service = factory(_ctx())

    assert isinstance(service, SingleboxDeviceSyncService)
    assert isinstance(service._delegate, BaasDeviceSyncService)
    assert isinstance(service._delegate._transport, BaasInvokeTransport)


# ── teclaw factory component ─────────────────────────────────────────────


def test_teclaw_factory_builds_the_whole_artifact_service():
    service = _teclaw_factory()(_ctx(provider="teclaw"))

    assert isinstance(service, TeclawDeviceSyncService)


def test_teclaw_factory_takes_identity_from_the_bot_row():
    """``entity_id`` (compose scope) and ``owner_id`` (binding lookup) come off
    the ``ac_bots`` row and stay distinct."""
    service = _teclaw_factory()(_ctx(provider="teclaw"))

    assert service._entity_id == "staff_u1"
    assert service._owner_id == "u1"
    assert service._user_id == "u1"
    assert service._bot_name == "助手"
    assert service._entity_type == "staff"
    assert service._engine_type == "teclaw"


def test_teclaw_factory_falls_back_to_ctx_identity_when_the_bot_row_is_missing():
    service = _teclaw_factory(bot_row={})(_ctx(provider="teclaw"))

    assert service._owner_id == "u1"
    assert service._entity_id == "u1"
    assert service._engine_type == "teclaw"
    assert service._entity_type == "staff"


def test_bot_row_lookup_failure_degrades_instead_of_escaping_the_factory():
    """The factory runs inside ``dispatch``, whose callers only expect
    ``DeviceSyncUnavailableError``; a repo error must not escape it."""
    bot_repo = MagicMock()
    bot_repo.get_by_id.side_effect = RuntimeError("db down")
    factory = TeclawDeviceSyncModule().teclaw_device_sync_factory(
        baas_service=MagicMock(),
        bot_repo=bot_repo,
        http_client=MagicMock(),
        composer=MagicMock(),
        draft_recorder=lambda: MagicMock(),
    )

    service = factory(_ctx(provider="teclaw"))

    assert isinstance(service, TeclawDeviceSyncService)
    assert service._owner_id == "u1"


def test_teclaw_factory_hands_the_composer_to_the_service_as_a_thunk():
    """The service defers compose until a delivery happens, so it takes a
    ``Callable[[], ConfigComposer]`` rather than the composer itself."""
    composer = MagicMock()
    factory = TeclawDeviceSyncModule().teclaw_device_sync_factory(
        baas_service=MagicMock(),
        bot_repo=MagicMock(**{"get_by_id.return_value": _bot_row()}),
        http_client=MagicMock(),
        composer=composer,
        draft_recorder=lambda: MagicMock(),
    )

    service = factory(_ctx(provider="teclaw"))

    assert service._composer_provider() is composer


# ── dispatcher routing ───────────────────────────────────────────────────


def test_dispatcher_routes_each_provider_to_its_own_factory():
    baas_factory = MagicMock(return_value=MagicMock())
    teclaw_factory = MagicMock(return_value=MagicMock())
    dispatcher = CommunityDeviceSyncModule().device_sync_dispatcher(
        baas_factory, teclaw_factory
    )
    assert isinstance(dispatcher, CommunityDeviceSyncDispatcher)

    baas_ctx, teclaw_ctx = _ctx(), _ctx(provider="teclaw")
    assert dispatcher.dispatch(baas_ctx) is baas_factory.return_value
    assert dispatcher.dispatch(teclaw_ctx) is teclaw_factory.return_value

    baas_factory.assert_called_once_with(baas_ctx)
    teclaw_factory.assert_called_once_with(teclaw_ctx)


# ── real DI graph ────────────────────────────────────────────────────────


def test_both_factories_resolve_through_the_real_injector(test_injector):
    """A collaborator unbound in some profile would only surface here, not in
    the hand-built factories above."""
    assert isinstance(test_injector.get(BaasDeviceSyncFactory), BaasDeviceSyncFactory)
    assert isinstance(test_injector.get(TeclawDeviceSyncFactory), TeclawDeviceSyncFactory)


def test_real_dispatcher_builds_a_fully_wired_teclaw_service(test_injector):
    service = test_injector.get(DeviceSyncDispatcher).dispatch(_ctx(provider="teclaw"))

    assert isinstance(service, TeclawDeviceSyncService)
    assert isinstance(service._composer_provider(), ConfigComposer)
    assert isinstance(service._draft_recorder(), BotPublishService)
    assert service._http_client is not None


def test_real_dispatcher_builds_a_baas_service(test_injector):
    service = test_injector.get(DeviceSyncDispatcher).dispatch(_ctx())

    assert isinstance(service, (BaasDeviceSyncService, SingleboxDeviceSyncService))


def test_bot_repository_is_the_real_binding(test_injector):
    """The teclaw factory reads the bot row through the injected repo."""
    assert test_injector.get(BotRepository) is not None


def test_dispatcher_factory_raises_the_expected_error_for_an_unrouted_provider():
    """Guards against drift between the route map here and the dispatcher's
    supported-provider set: a miss must surface as the error dispatch callers
    already handle, not a bare KeyError."""
    import pytest

    from agentclaw.community.core.devices.services.device_context import (
        UnknownProviderError,
    )

    module = CommunityDeviceSyncModule()
    dispatcher = module.device_sync_dispatcher(MagicMock(), MagicMock())
    # Reach past the dispatcher's own guard to the routing closure underneath.
    route = dispatcher._device_sync_factory

    with pytest.raises(UnknownProviderError, match=r"no DeviceSync factory"):
        route(_ctx(provider="arca"))
