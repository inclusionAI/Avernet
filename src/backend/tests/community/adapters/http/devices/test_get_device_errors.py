"""Tests for get_device handler error mapping."""

import asyncio
from unittest.mock import MagicMock

from sqlalchemy.exc import OperationalError

from agentclaw.community.adapters.http.devices.router import get_device
from agentclaw.community.core.devices.errors import DeviceServiceError


def _user():
    u = MagicMock()
    u.staffId = "123456"
    return u


def _call(service):
    """Invoke the async handler synchronously with mocked deps."""
    return asyncio.run(
        get_device(
            binding_id=1347313,
            user=_user(),
            service=service,
        )
    )


def test_get_device_service_error_returns_structured_response():
    service = MagicMock()
    service.get_device.side_effect = DeviceServiceError("device backend failed")

    resp = _call(service)

    assert resp.success is False
    assert resp.error_code == 50000
    assert resp.data is None
    assert resp.message == "device backend failed"


def test_get_device_operational_error_returns_structured_response():
    service = MagicMock()
    service.get_device.side_effect = OperationalError(
        "SELECT * FROM device_bindings WHERE id = ?",
        {"id": 1347313},
        Exception("database is locked"),
    )

    resp = _call(service)

    assert resp.success is False
    assert resp.error_code == 50000
    assert resp.data is None
    assert resp.message == "获取设备失败，请稍后重试"
