"""Physical command adapter contract; no real provider requests."""
from unittest.mock import Mock

import pytest

from agentclaw.community.core.service_bot.baas_service_errors import BaasServiceError
from agentclaw.community.core.service_bot.services.device_commands import DeviceCommandsMixin


def service(envelope):
    svc = DeviceCommandsMixin()
    svc._http = Mock()
    svc._http.post.return_value.json.return_value = envelope
    return svc


def test_pinned_device_command_uses_existing_paas_endpoint():
    result = {'exit_code': 0, 'stdout': 'legacy'}
    svc = service({'code': 0, 'data': result})
    assert svc.exec_command_on_device(paas_device_id='physical@template', cmd='probe') == result
    svc._http.post.assert_called_once_with(
        '/api/v1/paas/devices/physical@template/commands', json={'cmd': 'probe'}, timeout=40.0)
    svc._http.post.return_value.raise_for_status.assert_called_once()


@pytest.mark.parametrize('envelope', [None, {}, {'code': 1}, {'code': 0, 'data': None}])
def test_invalid_result_is_not_legacy(envelope):
    svc = service(envelope)
    with pytest.raises(BaasServiceError):
        svc.exec_command_on_device(paas_device_id='physical', cmd='probe')


def test_device_id_cannot_inject_url_path():
    svc = service({'code': 0, 'data': {'exit_code': 0}})
    svc.exec_command_on_device(paas_device_id='device/other?x=1', cmd='probe')
    assert svc._http.post.call_args.args[0] == '/api/v1/paas/devices/device%2Fother%3Fx%3D1/commands'


def test_transport_error_propagates():
    svc = service({})
    svc._http.post.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        svc.exec_command_on_device(paas_device_id='physical', cmd='probe')
