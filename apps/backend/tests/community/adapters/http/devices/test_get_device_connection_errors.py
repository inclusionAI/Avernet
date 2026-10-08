"""Tests for get_device_connection handler error mapping.

The frontend bot list / chat entry calls GET /api/v1/devices/{binding_id}/connection.
When BaaS reports the device is not ready (503 NO_ACTIVE_DEVICES), the leaf
raises BaasDeviceServiceError (a DeviceServiceError subclass). The handler must
NOT let it bubble into a framework 500 — it should return a friendly ApiResponse
so the frontend can show "设备未就绪，请稍候" and monitoring is not polluted by 5xx.
"""
import asyncio
from unittest.mock import MagicMock

from agentclaw.community.adapters.http.devices.router import get_device_connection
from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceServiceError


def _user():
    u = MagicMock()
    u.staffId = "123456"
    return u


def _call(service):
    """Invoke the async handler synchronously with mocked deps."""
    return asyncio.run(
        get_device_connection(
            binding_id=1347313,
            port=None,
            ttl=None,
            user=_user(),
            service=service,
        )
    )


def test_no_active_devices_returns_friendly_response_not_500():
    """NO_ACTIVE_DEVICES → success=False with a device-not-ready code, no exception."""
    service = MagicMock()
    service.get_device_connection.side_effect = BaasDeviceServiceError(
        'Failed to get device connection: BaaS API error: 503 - '
        '{"detail":{"error":"NO_ACTIVE_DEVICES","message":"No active devices found for bot",'
        '"bot_uuid":"BOT-d324d33613a34c6eae48896c4db6d30a"}}'
    )

    resp = _call(service)

    assert resp.success is False
    assert resp.error_code == 40303
    assert resp.data is None
    # friendly, user-facing — not a raw stack/JSON dump
    assert "设备" in resp.message


def test_generic_baas_failure_returns_friendly_response_not_500():
    """Any other BaaS/device failure also degrades to a friendly response, not 500."""
    service = MagicMock()
    service.get_device_connection.side_effect = BaasDeviceServiceError(
        "Failed to get device connection: BaaS API error: 502 - bad gateway"
    )

    resp = _call(service)

    assert resp.success is False
    assert resp.error_code == 50000
    assert resp.data is None
    assert resp.message
