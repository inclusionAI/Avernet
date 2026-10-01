"""Tests for the provider-inventory device endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user, require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.devices.router import router
from agentclaw.community.api.device_service import DeviceServiceProtocol


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


def test_provider_inventory_endpoint_returns_service_result():
    service = MagicMock()
    service.get_provider_inventory.return_value = {
        "total": 3,
        "scanned": 3,
        "truncated": False,
        "by_provider": {"arca": {"total": 2}, "baas": {"total": 1}},
    }
    client = _client(service)

    response = client.get(
        "/api/v1/devices/provider-inventory",
        params={
            "entity_type": "staff",
            "env": "prod",
            "status": "ACTIVE",
            "page_size": 50,
            "max_pages": 3,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["by_provider"]["arca"]["total"] == 2
    service.get_provider_inventory.assert_called_once_with(
        entity_id=None,
        entity_type="staff",
        env="prod",
        status="ACTIVE",
        page_size=50,
        max_pages=3,
    )
