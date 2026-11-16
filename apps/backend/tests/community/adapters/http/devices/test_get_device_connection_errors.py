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


def test_unavailable_subtype_preserves_both_legacy_device_routes():
    from agentclaw.community.adapters.http.devices.router import get_device_connection_by_bot
    from agentclaw.community.core.devices.errors import DeviceUnavailableError
    from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceUnavailableError
    from agentclaw.community.core.service_bot.baas_service_errors import BaasNoActiveDevicesError

    cause = BaasNoActiveDevicesError(status_code=404)
    error = BaasDeviceUnavailableError(f"Failed to get device connection: {cause}")
    error.__cause__ = cause
    assert isinstance(error, (BaasDeviceServiceError, DeviceUnavailableError))
    assert isinstance(error, BaasDeviceServiceError)
    assert isinstance(error, DeviceUnavailableError)
    assert error.__cause__ is cause
    service = MagicMock()
    service.get_device_connection.side_effect = error
    service.get_device_connection_by_bot.side_effect = error
    first = _call(service)
    second = asyncio.run(get_device_connection_by_bot(
        bot_id="bot", port=None, device_uuid=None, ws_conn_mode=None,
        default_tag=None, user=_user(), service=service,
    ))
    assert first.error_code == second.error_code == 40303
    assert first.success is second.success is False
