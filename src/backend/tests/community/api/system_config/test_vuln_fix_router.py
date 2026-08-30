"""Tests for system_config router security fixes (#75, #79).

Verifies that delete_config and delete_category require operator privileges.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user, require_operator
from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.system_config import DeviceConfigService, SystemConfigService


# --- Helpers ---

def _bind_services(mock_config_service, mock_device_config_service):
    """Bind mock services via injector Module.

    Router endpoints declare their deps via the Service API Protocols
    (post-R8), so the mocks are bound under both the Protocol type
    (what the router resolves) and the concrete class (so any other
    consumer in the test injector graph still works).
    """
    from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
    from agentclaw.community.api.device_config_service import DeviceConfigServiceProtocol

    class _M(Module):
        def configure(self, binder):
            binder.bind(SystemConfigService, to=mock_config_service)
            binder.bind(DeviceConfigService, to=mock_device_config_service)
            binder.bind(SystemConfigServiceProtocol, to=mock_config_service)
            binder.bind(DeviceConfigServiceProtocol, to=mock_device_config_service)
    return _M()


# --- Fixtures ---

@pytest.fixture
def normal_user():
    return AuthenticatedIdentity(id="1", operatorName="normal_user", outUserNo="100011", nickName="NormalUser")


@pytest.fixture
def operator_user():
    return AuthenticatedIdentity(id="2", operatorName="operator_user", outUserNo="100000", nickName="OperatorUser")


@pytest.fixture
def mock_config_service():
    svc = MagicMock()
    svc.delete_config_by_key = MagicMock(return_value=True)
    svc.delete_category = MagicMock(return_value=True)
    return svc


@pytest.fixture
def mock_device_config_service():
    return MagicMock()


def _make_app_with_overrides(user, allow_operator: bool, mock_config_service, mock_device_config_service):
    """Build a FastAPI app with auth overrides for require_operator testing.

    Args:
        user: The AuthenticatedIdentity to inject.
        allow_operator: If True, require_operator passes; if False, it returns 403.
        mock_config_service: Mocked config service.
        mock_device_config_service: Mocked device config service.
    """
    from agentclaw.community.adapters.http.system_config.router import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user

    if allow_operator:
        app.dependency_overrides[require_operator] = lambda: user
    else:
        async def _reject_operator():
            raise HTTPException(status_code=403, detail="权限不足：您没有操作员权限")
        app.dependency_overrides[require_operator] = _reject_operator

    attach_injector(app, Injector([_bind_services(mock_config_service, mock_device_config_service)]))
    return app


@pytest.fixture
def client_with_normal_user(normal_user, mock_config_service, mock_device_config_service):
    """Client with normal (non-operator) user — require_operator returns 403."""
    app = _make_app_with_overrides(normal_user, allow_operator=False, mock_config_service=mock_config_service, mock_device_config_service=mock_device_config_service)
    return TestClient(app)


@pytest.fixture
def client_with_operator_user(operator_user, mock_config_service, mock_device_config_service):
    """Client with operator (admin) user — require_operator passes."""
    app = _make_app_with_overrides(operator_user, allow_operator=True, mock_config_service=mock_config_service, mock_device_config_service=mock_device_config_service)
    return TestClient(app)


# --- Tests for #75: delete_config requires operator ---

class TestDeleteConfigRequiresOperator:
    """POST /api/v1/config/delete — only operators can delete configs."""

    def test_delete_config_with_operator_succeeds(self, client_with_operator_user, mock_config_service):
        mock_config_service.delete_config_by_key.return_value = True
        resp = client_with_operator_user.post(
            "/api/v1/config/delete",
            json={"category": "device", "config_key": "test_key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_delete_config_with_normal_user_forbidden(self, client_with_normal_user):
        """Non-operator users should get 403 Forbidden."""
        resp = client_with_normal_user.post(
            "/api/v1/config/delete",
            json={"category": "device", "config_key": "test_key"},
        )
        assert resp.status_code == 403

    def test_delete_config_not_found(self, client_with_operator_user, mock_config_service):
        mock_config_service.delete_config_by_key.return_value = False
        resp = client_with_operator_user.post(
            "/api/v1/config/delete",
            json={"category": "device", "config_key": "nonexistent"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 40401


# --- Tests for #79: delete_category requires operator ---

class TestDeleteCategoryRequiresOperator:
    """POST /api/v1/config/categories/delete — only operators can delete categories."""

    def test_delete_category_with_operator_succeeds(self, client_with_operator_user, mock_config_service):
        mock_config_service.delete_category.return_value = True
        resp = client_with_operator_user.post(
            "/api/v1/config/categories/delete",
            json={"category_id": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_delete_category_with_normal_user_forbidden(self, client_with_normal_user):
        """Non-operator users should get 403 Forbidden."""
        resp = client_with_normal_user.post(
            "/api/v1/config/categories/delete",
            json={"category_id": 1},
        )
        assert resp.status_code == 403

    def test_delete_category_not_found(self, client_with_operator_user, mock_config_service):
        mock_config_service.delete_category.return_value = False
        resp = client_with_operator_user.post(
            "/api/v1/config/categories/delete",
            json={"category_id": 999999},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 40401