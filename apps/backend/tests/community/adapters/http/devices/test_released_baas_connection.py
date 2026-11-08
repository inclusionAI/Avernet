"""The BaaS lifecycle guard uses the existing invalid-status API envelope."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.adapters.http.devices.router import get_device_connection
from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceService
from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter


def test_released_baas_connection_returns_existing_invalid_status_code():
    repo = MagicMock()
    repo.get_by_id.return_value = SimpleNamespace(status="RELEASED", device_provider="baas")
    provider = object.__new__(BaasDeviceService)
    provider._repo = repo
    provider._baas_service = MagicMock()
    service = object.__new__(DeviceServiceRouter)
    service._repo = repo
    service._providers = {"baas": provider}
    service._default_service = provider
    user = MagicMock()
    user.staffId = "owner"

    response = asyncio.run(get_device_connection(
        binding_id=7, port=None, ttl=None, device_uuid=None, ws_conn_mode=None,
        user=user, service=service,
    ))

    assert response.success is False
    assert response.error_code == 40301
    assert response.data is None
    provider._baas_service.get_ws_info.assert_not_called()
