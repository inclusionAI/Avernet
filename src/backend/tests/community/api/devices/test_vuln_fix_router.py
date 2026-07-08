"""Tests for devices router security fix (#107).

Verifies that list_connectable_devices enforces entity_id ownership check.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.adapters.http.devices.dependencies import get_operator_context
from agentclaw.community.core.devices.services.device_service import DeviceService


# --- Helpers ---

def _bind_device_service(mock_device_service):
    """Bind a mock DeviceService via injector Module."""
    from agentclaw.community.api.device_service import DeviceServiceProtocol
    class _M(Module):
        def configure(self, binder):
            binder.bind(DeviceService, to=mock_device_service)
            binder.bind(DeviceServiceProtocol, to=mock_device_service)
    return _M()


# --- Fixtures ---

@pytest.fixture
def user_a():
    return AuthenticatedIdentity(id="1", operatorName="user_a", outUserNo="100011", nickName="UserA")


@pytest.fixture
def mock_operator_context():
    return MagicMock(staff_id="100011", staff="100011", nick_name="UserA", operator_name="UserA", tenant_id="default")


@pytest.fixture
def mock_device_service():
    from agentclaw.community.core.devices.models import DeviceBindingInfo

    svc = MagicMock()
    mock_record = MagicMock()
    mock_record.entity_id = "100011"
    mock_record.device_id = "staff_100011_default_test"
    mock_record.status = "ACTIVE"
    mock_record.entity_type = "staff"
    mock_record.device_provider = "arca"
    mock_record.env = "dev"

    binding_info = DeviceBindingInfo(record=mock_record, connection=None)
    svc.list_connectable_devices = MagicMock(return_value=(1, [binding_info]))
    return svc


@pytest.fixture
def client(user_a, mock_device_service, mock_operator_context):
    from agentclaw.community.adapters.http.devices.router import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_operator_context] = lambda u=MagicMock(): mock_operator_context
    attach_injector(app, Injector([_bind_device_service(mock_device_service)]))
    return TestClient(app)


# --- Tests for #107: connectable devices entity_id ownership check ---

class TestListConnectableDevicesOwnership:
    """GET /api/v1/devices/connectable — entity_id must match authenticated user."""

    def test_list_own_devices_succeeds(self, client, mock_device_service):
        """User querying their own entity_id should succeed."""
        resp = client.get(
            "/api/v1/devices/connectable",
            params={"entity_id": "100011", "entity_type": "staff"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_list_other_user_devices_forbidden(self, client):
        """User querying another user's entity_id should get 403."""
        resp = client.get(
            "/api/v1/devices/connectable",
            params={"entity_id": "100012", "entity_type": "staff"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 403
        assert "无权" in data["message"]

    def test_list_devices_unauthenticated(self, mock_device_service, mock_operator_context):
        """Unauthenticated requests should be rejected."""
        from agentclaw.community.adapters.http.devices.router import router

        app = FastAPI()
        app.include_router(router)
        attach_injector(app, Injector([_bind_device_service(mock_device_service)]))
        app.dependency_overrides[get_operator_context] = lambda u=MagicMock(): mock_operator_context
        # No get_current_user override — auth will fail with 401
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(
            "/api/v1/devices/connectable",
            params={"entity_id": "100011", "entity_type": "staff"},
        )
        assert resp.status_code in (401, 403) or resp.status_code >= 400