"""Released bindings must never ask BaaS to reconnect a deleted runtime."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.errors import InvalidDeviceStatusError
from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceService
from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter


@pytest.fixture
def connection_service():
    service = object.__new__(BaasDeviceService)
    service._repo = MagicMock()
    service._baas_service = MagicMock()
    service._default_engine = "openclaw"
    service._resolve_bot_by_binding_id = MagicMock(return_value=None)
    service._baas_service.get_ws_info.return_value = SimpleNamespace(
        target="runtime:20003", token="test-token", ws_url="wss://example.invalid/ws",
        baas_base_url="https://example.invalid", bot_uuid="bot-test", tenant="test",
        engine_port=20003, expires_at="2099-01-01T00:00:00Z",
    )
    return service


def test_released_binding_is_rejected_before_upstream(connection_service):
    connection_service._repo.get_by_id.return_value = SimpleNamespace(status="RELEASED")

    with pytest.raises(InvalidDeviceStatusError, match="released device"):
        connection_service.get_device_connection(
            binding_id=7, operator=OperatorContext(staff_id="owner", staff="Owner", nick_name="Owner", operator_name="Owner"),
        )

    connection_service._baas_service.get_ws_info.assert_not_called()


@pytest.mark.parametrize("status", ["ACTIVE", "PENDING", "FAILED", "STOPPED"])
def test_other_states_keep_existing_provider_behavior(connection_service, status):
    connection_service._repo.get_by_id.return_value = SimpleNamespace(status=status)

    result = connection_service.get_device_connection(
        binding_id=7, operator=OperatorContext(staff_id="owner", staff="Owner", nick_name="Owner", operator_name="Owner"),
        device_uuid="instance", ws_conn_mode="relay", path="/api/claude_code/ws",
    )

    assert result.url == "wss://example.invalid/ws"
    connection_service._baas_service.get_ws_info.assert_called_once_with(
        bind_id=7, device_affinity="owner", device_uuid="instance",
        ws_conn_mode="relay", path="/api/claude_code/ws",
    )


def test_missing_binding_does_not_change_existing_lookup_path(connection_service):
    connection_service._repo.get_by_id.return_value = None
    connection_service.get_device_connection(
        binding_id=7, operator=OperatorContext(staff_id="owner", staff="Owner", nick_name="Owner", operator_name="Owner"),
    )
    connection_service._baas_service.get_ws_info.assert_called_once()


def test_router_delegation_preserves_released_binding_rejection(connection_service):
    connection_service._repo.get_by_id.return_value = SimpleNamespace(
        status="RELEASED", device_provider="baas",
    )
    router = object.__new__(DeviceServiceRouter)
    router._repo = connection_service._repo
    router._providers = {"baas": connection_service}
    router._default_service = connection_service

    with pytest.raises(InvalidDeviceStatusError):
        router.get_device_connection(
            binding_id=7, operator=OperatorContext(staff_id="owner", staff="Owner", nick_name="Owner", operator_name="Owner"),
        )

    connection_service._baas_service.get_ws_info.assert_not_called()
