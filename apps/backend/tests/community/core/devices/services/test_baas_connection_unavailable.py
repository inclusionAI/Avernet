"""Provider-neutral unavailable errors preserve the existing BaaS contract."""

from unittest.mock import Mock

import pytest

from agentclaw.community.core.devices.errors import DeviceUnavailableError
from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.services.baas_device_service import (
    BaasDeviceService,
    BaasDeviceServiceError,
    BaasDeviceUnavailableError,
)
from agentclaw.community.core.service_bot.baas_service_errors import (
    BaasNoActiveDevicesError,
    BaasServiceError,
)


@pytest.mark.parametrize("status", [404, 503])
def test_offline_preserves_message_cause_and_both_error_contracts(status):
    repo = Mock()
    repo.get_by_id.return_value.status = "ACTIVE"
    baas = Mock()
    cause = BaasNoActiveDevicesError(status_code=status)
    baas.get_ws_info.side_effect = cause
    service = BaasDeviceService(
        repository=repo, baas_service=baas, bot_query=Mock(), bot_sync=Mock(),
        oss_record_repo=Mock(), mcp_sync=Mock(), template_resolver=Mock(),
        layout_confirmation=Mock(),
    )
    operator = OperatorContext(staff_id="owner", staff="owner", nick_name="Owner", operator_name="Owner", tenant_id="test")
    with pytest.raises(BaasDeviceUnavailableError) as raised:
        service.get_device_connection(binding_id=1, operator=operator)
    assert isinstance(raised.value, BaasDeviceServiceError)
    assert isinstance(raised.value, DeviceUnavailableError)
    assert raised.value.__cause__ is cause
    assert str(raised.value) == f"Failed to get device connection: {cause}"


def test_other_baas_errors_remain_generic():
    repo = Mock()
    repo.get_by_id.return_value.status = "ACTIVE"
    baas = Mock()
    baas.get_ws_info.side_effect = BaasServiceError("upstream failure")
    service = BaasDeviceService(
        repository=repo, baas_service=baas, bot_query=Mock(), bot_sync=Mock(),
        oss_record_repo=Mock(), mcp_sync=Mock(), template_resolver=Mock(),
        layout_confirmation=Mock(),
    )
    operator = OperatorContext(staff_id="owner", staff="owner", nick_name="Owner", operator_name="Owner", tenant_id="test")
    with pytest.raises(BaasDeviceServiceError) as raised:
        service.get_device_connection(binding_id=1, operator=operator)
    assert not isinstance(raised.value, DeviceUnavailableError)
