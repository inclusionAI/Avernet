"""Connection classification retains the BaaS offline cause."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.engine_runtime.connection import EngineConnectionService
from agentclaw.community.core.engine_runtime.errors import EngineDeviceNotReadyError, EngineUpstreamError
from agentclaw.community.di.config import GatewayEndpoint


def _svc(devices):
    bots = Mock()
    bots.get_bot_by_address.return_value = {"id": 1, "bot_id": "bot", "owner_id": "owner", "bot_type": "personal", "active_engine": "openclaw", "public": "0"}
    bindings = Mock()
    bindings.get_active_by_bot_and_owner.return_value = SimpleNamespace(id=42)
    collaborators = Mock()
    collaborators.get_operable_permission_level.return_value = PermissionLevel.OWNER
    return EngineConnectionService(bots, bindings, devices, GatewayEndpoint(base_url="https://gw.example"), collaborators, Mock(), desktop_connections=Mock())


def _build(svc):
    return svc.build(bot_id="bot", owner_id="owner", caller_id="owner", stage="draft")


class _Devices:
    raises: Exception | None = None

    def get_device_connection(self, **kwargs):
        raise self.raises


def test_provider_neutral_unavailable_is_not_ready():
    from agentclaw.community.core.devices.errors import DeviceUnavailableError

    devices = _Devices()
    devices.raises = DeviceUnavailableError("unavailable")
    with pytest.raises(EngineDeviceNotReadyError):
        _build(_svc(devices=devices))


@pytest.mark.parametrize("status_code", [404, 503])
def test_direct_baas_offline_remains_not_ready(status_code):
    from agentclaw.community.core.service_bot.baas_service_errors import BaasNoActiveDevicesError

    devices = _Devices()
    devices.raises = BaasNoActiveDevicesError(status_code=status_code)
    with pytest.raises(EngineDeviceNotReadyError):
        _build(_svc(devices=devices))


def test_provider_error_message_alone_does_not_mean_offline():
    from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceServiceError

    devices = _Devices()
    devices.raises = BaasDeviceServiceError("NO_ACTIVE_DEVICES")
    with pytest.raises(EngineUpstreamError):
        _build(_svc(devices=devices))


def test_other_device_error_with_offline_cause_remains_upstream():
    from agentclaw.community.core.devices.errors import DeviceServiceError
    from agentclaw.community.core.service_bot.baas_service_errors import BaasNoActiveDevicesError

    devices = _Devices()
    devices.raises = DeviceServiceError("another provider failed")
    devices.raises.__cause__ = BaasNoActiveDevicesError(status_code=404)
    with pytest.raises(EngineUpstreamError):
        _build(_svc(devices=devices))
