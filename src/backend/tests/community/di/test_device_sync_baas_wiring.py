from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService
from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport
from agentclaw.community.core.devices.services.singlebox_device_sync import SingleboxDeviceSyncService
from agentclaw.community.di.modules.infrastructure.community.device_sync import CommunityDeviceSyncModule
from agentclaw.community.plugins.community.device_sync_dispatcher import CommunityDeviceSyncDispatcher


def _ctx():
    return SimpleNamespace(
        provider="baas", bot_id="b", bot_type="personal",
        conn_info={"bind_id": 1, "engine_port": 20003, "tenant": "", "paas_device_id": "u"},
    )


def test_community_dispatcher_factory_builds_shared_baas_service():
    dispatcher = CommunityDeviceSyncModule().device_sync_dispatcher(MagicMock())
    service = dispatcher.dispatch(_ctx())
    assert isinstance(dispatcher, CommunityDeviceSyncDispatcher)
    assert isinstance(service, BaasDeviceSyncService)
    assert isinstance(service._transport, BaasInvokeTransport)


def test_singlebox_dispatcher_factory_wraps_shared_baas_service():
    dispatcher = CommunityDeviceSyncModule(
        device_sync_wrapper=SingleboxDeviceSyncService,
    ).device_sync_dispatcher(MagicMock())
    service = dispatcher.dispatch(_ctx())
    assert isinstance(dispatcher, CommunityDeviceSyncDispatcher)
    assert isinstance(service, SingleboxDeviceSyncService)
    assert isinstance(service._delegate, BaasDeviceSyncService)
    assert isinstance(service._delegate._transport, BaasInvokeTransport)
