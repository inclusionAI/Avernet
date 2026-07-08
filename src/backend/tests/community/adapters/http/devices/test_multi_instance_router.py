"""Tests for §1 multi-instance list endpoints (bot_id / binding_id entry)
plus §2 restart endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user, require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.devices.router import router
from agentclaw.community.adapters.http.devices.schemas import RestartDeviceRequest
from agentclaw.community.api.device_service import DeviceServiceProtocol
from agentclaw.community.core.devices.errors import InvalidDeviceStatusError
from agentclaw.community.core.devices.models import DeviceConnectionInfo
from agentclaw.community.core.devices.services.device_service_router import (
    BindingNotFoundError,
    BotPublishNotFoundError,
)
from pydantic import ValidationError


def _operator() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="1",
        staffId="100001",
        operatorName="operator",
        nickName="Operator",
    )


def _client(service: MagicMock) -> TestClient:
    class _M(Module):
        def configure(self, binder):
            binder.bind(DeviceServiceProtocol, to=service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _operator
    app.dependency_overrides[require_operator] = _operator
    attach_injector(app, Injector([_M()]))
    return TestClient(app)


def _instances_payload() -> dict:
    return {
        "bot_uuid": "bot-uuid-abc",
        "devices": [
            {
                "device_uuid": "DEVICE-001",
                "status": "ACTIVE",
                "health": "true",
                "provider_type": "baas",
                "provider_device_id": "prov-dev-001",
                "gmt_create": "2024-01-01T00:00:00Z",
                "health_status": "ACTIVE",
                "engine_type": "openclaw",
                "bot_uuid": "bot-uuid-abc",
            }
        ],
    }


# ---------------------------------------------------------------------------
# bot_id entry: GET /api/v1/devices/bots/{bot_id}/instances
# ---------------------------------------------------------------------------


def test_get_instances_by_bot_returns_devices():
    service = MagicMock()
    service.get_instances_by_bot.return_value = _instances_payload()
    client = _client(service)

    resp = client.get(
        "/api/v1/devices/bots/bot-001/instances",
        params={"health_check": "true"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error_code"] == 200
    assert body["data"]["bot_uuid"] == "bot-uuid-abc"
    d = body["data"]["devices"][0]
    assert d["device_uuid"] == "DEVICE-001"
    assert d["health_status"] == "ACTIVE"
    assert d["engine_type"] == "openclaw"
    service.get_instances_by_bot.assert_called_once_with(
        bot_id="bot-001", health_check=True
    )


def test_get_instances_by_bot_defaults_health_check_false():
    service = MagicMock()
    service.get_instances_by_bot.return_value = _instances_payload()
    client = _client(service)

    resp = client.get("/api/v1/devices/bots/bot-001/instances")

    assert resp.status_code == 200
    service.get_instances_by_bot.assert_called_once_with(
        bot_id="bot-001", health_check=False
    )


def test_get_instances_by_bot_resolution_failure_returns_40403():
    service = MagicMock()
    service.get_instances_by_bot.side_effect = BotPublishNotFoundError("no publish")
    client = _client(service)

    resp = client.get("/api/v1/devices/bots/bot-001/instances")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_code"] == 40403
    assert body["data"] is None


# ---------------------------------------------------------------------------
# binding_id entry: GET /api/v1/devices/{binding_id}/instances
# ---------------------------------------------------------------------------


def test_get_instances_by_binding_returns_devices():
    service = MagicMock()
    service.get_instances.return_value = _instances_payload()
    client = _client(service)

    resp = client.get(
        "/api/v1/devices/1001/instances",
        params={"health_check": "true"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["devices"][0]["device_uuid"] == "DEVICE-001"
    service.get_instances.assert_called_once_with(binding_id=1001, health_check=True)


def test_get_instances_by_binding_not_found_returns_40403():
    service = MagicMock()
    service.get_instances.side_effect = BindingNotFoundError("binding not found")
    client = _client(service)

    resp = client.get("/api/v1/devices/9999/instances")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_code"] == 40403


def test_get_instances_by_binding_unexpected_error_returns_50000():
    service = MagicMock()
    service.get_instances.side_effect = ValueError("boom")
    client = _client(service)

    resp = client.get("/api/v1/devices/1001/instances")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_code"] == 50000


# ---------------------------------------------------------------------------
# §2 restart: POST /api/v1/devices/{binding_id}/restart
# ---------------------------------------------------------------------------


def test_restart_device_success_returns_publish_id():
    service = MagicMock()
    service.restart_device.return_value = {"publish_id": 42}
    client = _client(service)

    resp = client.post(
        "/api/v1/devices/1001/restart",
        json={"device_uuid": "DEVICE-001"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error_code"] == 200
    assert body["data"] == {"publish_id": 42}
    call = service.restart_device.call_args
    assert call.kwargs["binding_id"] == 1001
    assert call.kwargs["device_uuid"] == "DEVICE-001"


def test_restart_device_non_owner_returns_40301():
    service = MagicMock()
    service.restart_device.side_effect = InvalidDeviceStatusError("仅 Bot 所有者可重启设备实例")
    client = _client(service)

    resp = client.post(
        "/api/v1/devices/1001/restart",
        json={"device_uuid": "DEVICE-001"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_code"] == 40301
    assert body["data"] is None


def test_restart_device_publish_conflict_returns_40901():
    service = MagicMock()
    service.restart_device.side_effect = RuntimeError(
        "BaaS API error: 409 - PUBLISH_CONFLICT"
    )
    client = _client(service)

    resp = client.post(
        "/api/v1/devices/1001/restart",
        json={"device_uuid": "DEVICE-001"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_code"] == 40901


def test_restart_device_device_not_found_returns_40404():
    service = MagicMock()
    service.restart_device.side_effect = RuntimeError(
        "BaaS API error: 404 - DEVICE_NOT_FOUND"
    )
    client = _client(service)

    resp = client.post(
        "/api/v1/devices/1001/restart",
        json={"device_uuid": "DEVICE-001"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_code"] == 40404


def test_restart_device_bot_not_found_returns_40401():
    service = MagicMock()
    service.restart_device.side_effect = RuntimeError(
        "BaaS API error: 404 - BOT_NOT_FOUND"
    )
    client = _client(service)

    resp = client.post(
        "/api/v1/devices/1001/restart",
        json={"device_uuid": "DEVICE-001"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_code"] == 40401


def test_restart_device_binding_not_found_returns_40403():
    service = MagicMock()
    service.restart_device.side_effect = BindingNotFoundError("Binding 1001 not found")
    client = _client(service)

    resp = client.post(
        "/api/v1/devices/1001/restart",
        json={"device_uuid": "DEVICE-001"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_code"] == 40403


def test_restart_device_missing_device_uuid_returns_422():
    service = MagicMock()
    client = _client(service)

    resp = client.post("/api/v1/devices/1001/restart", json={})

    assert resp.status_code == 422
    service.restart_device.assert_not_called()


def test_restart_device_request_schema_requires_device_uuid():
    assert RestartDeviceRequest(device_uuid="DEVICE-001").device_uuid == "DEVICE-001"
    with pytest.raises(ValidationError):
        RestartDeviceRequest()


# ---------------------------------------------------------------------------
# §3 conn-info bot_id entry: GET /api/v1/devices/bots/{bot_id}/connection
# ---------------------------------------------------------------------------


def _conn_info() -> DeviceConnectionInfo:
    return DeviceConnectionInfo(
        type="baas",
        target="ARCA_sandbox-123:8080",
        token="tok-abc",
        engine_type="openclaw",
    )


def test_get_connection_by_bot_passes_device_uuid_and_returns_target():
    service = MagicMock()
    service.get_device_connection_by_bot.return_value = _conn_info()
    client = _client(service)

    resp = client.get(
        "/api/v1/devices/bots/bot-001/connection",
        params={"device_uuid": "DEVICE-001", "port": 8080},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error_code"] == 200
    assert body["data"]["target"] == "ARCA_sandbox-123:8080"
    call = service.get_device_connection_by_bot.call_args
    assert call.kwargs["bot_id"] == "bot-001"
    assert call.kwargs["device_uuid"] == "DEVICE-001"
    assert call.kwargs["port"] == 8080


def test_get_connection_by_bot_without_device_uuid_is_backward_compatible():
    service = MagicMock()
    service.get_device_connection_by_bot.return_value = _conn_info()
    client = _client(service)

    resp = client.get("/api/v1/devices/bots/bot-001/connection")

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    call = service.get_device_connection_by_bot.call_args
    assert call.kwargs["device_uuid"] is None


def test_get_connection_by_bot_resolution_failure_returns_40403():
    service = MagicMock()
    service.get_device_connection_by_bot.side_effect = BotPublishNotFoundError(
        "no publish"
    )
    client = _client(service)

    resp = client.get("/api/v1/devices/bots/bot-001/connection")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_code"] == 40403
    assert body["data"] is None


def test_get_connection_by_bot_no_active_devices_returns_40303():
    from agentclaw.community.core.devices.errors import DeviceServiceError

    service = MagicMock()
    service.get_device_connection_by_bot.side_effect = DeviceServiceError(
        "BaaS error: 503 - NO_ACTIVE_DEVICES"
    )
    client = _client(service)

    resp = client.get(
        "/api/v1/devices/bots/bot-001/connection",
        params={"device_uuid": "DEVICE-404"},
    )

    assert resp.status_code == 200
    body = resp.json()
    # No silent fallback to another instance — surfaced as a business error code.
    assert body["success"] is False
    assert body["error_code"] == 40303
    assert body["data"] is None
