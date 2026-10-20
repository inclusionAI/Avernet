"""Optional exec timeouts preserve old callers and propagate to the provider."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter
from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceService
from agentclaw.community.core.devices.models import AllocatedDevice, DeviceBindingStatus


def test_router_forwards_explicit_timeout_but_omits_unspecified_keyword():
    router = DeviceServiceRouter.__new__(DeviceServiceRouter)
    provider = Mock()
    router._get_provider_for_device_id = Mock(return_value=provider)
    router.exec_shell_new('d', 'true')
    provider.exec_shell_new.assert_called_with('d', 'true')
    router.exec_shell_new('d', 'true', timeout_seconds=900)
    provider.exec_shell_new.assert_called_with('d', 'true', timeout_seconds=900)


def service():
    svc = DeviceService.__new__(DeviceService)
    svc._repo = Mock()
    svc._repo.get_by_device_id.return_value = SimpleNamespace(
        device_id='d', device_provider='baas', device_props={}, status=DeviceBindingStatus.ACTIVE.value)
    svc._exec_shell_new = Mock()
    return svc


def test_service_forwards_to_provider_hook():
    svc = service()
    svc.exec_shell_new('d', 'true', timeout_seconds=900)
    assert svc._exec_shell_new.call_args.kwargs['timeout_seconds'] == 900
    svc.exec_shell_new('d', 'true')
    assert 'timeout_seconds' not in svc._exec_shell_new.call_args.kwargs


@pytest.mark.parametrize('timeout', [0, -1, True, '900'])
def test_invalid_timeout_never_executes(timeout):
    svc = service()
    with pytest.raises(ValueError):
        svc.exec_shell_new('d', 'true', timeout_seconds=timeout)
    svc._exec_shell_new.assert_not_called()


def test_baas_provider_uses_seconds_and_preserves_30s_default():
    svc = BaasDeviceService.__new__(BaasDeviceService)
    svc._baas_service = Mock()
    svc._baas_service.exec_command_on_bot.return_value = {'exit_code': 0}
    device = AllocatedDevice(device_id='d', device_provider='baas', device_props={'bot_uuid': 'bot'})
    svc._exec_shell_new(device, 'true', timeout_seconds=900)
    svc._baas_service.exec_command_on_bot.assert_called_with(bot_uuid='bot', cmd='true', timeout_seconds=900)
    svc._exec_shell_new(device, 'true')
    svc._baas_service.exec_command_on_bot.assert_called_with(bot_uuid='bot', cmd='true', timeout_seconds=30)
