"""Tests for device switch management endpoints (poolab sandbox routing).

Covers:
- POST /api/v1/config/device/template-type-provider-map
- POST /api/v1/config/device/personal-bot-baas-disable

Both endpoints require operator privileges (require_operator).
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user, require_operator
from agentclaw.community.api.device_config_service import DeviceConfigServiceProtocol
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.system_config import DeviceConfigService, SystemConfigService


@pytest.fixture
def operator_user():
    return AuthenticatedIdentity(
        id="1", operatorName="test_user", outUserNo="100001", nickName="Tester"
    )


@pytest.fixture
def normal_user():
    return AuthenticatedIdentity(
        id="2", operatorName="normal_user", outUserNo="100002", nickName="Normal"
    )


@pytest.fixture
def mock_device_config_service():
    return MagicMock(spec=DeviceConfigService)


def _make_app(user, allow_operator: bool, mock_device_config_service):
    from agentclaw.community.adapters.http.system_config.router import router

    mock_config_service = MagicMock()

    class _M(Module):
        def configure(self, binder):
            binder.bind(SystemConfigService, to=mock_config_service)
            binder.bind(SystemConfigServiceProtocol, to=mock_config_service)
            binder.bind(DeviceConfigService, to=mock_device_config_service)
            binder.bind(DeviceConfigServiceProtocol, to=mock_device_config_service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user

    if allow_operator:
        app.dependency_overrides[require_operator] = lambda: user
    else:
        async def _reject():
            raise HTTPException(status_code=403, detail="权限不足：您没有操作员权限")
        app.dependency_overrides[require_operator] = _reject

    attach_injector(app, Injector([_M()]))
    return app


@pytest.fixture
def client(operator_user, mock_device_config_service):
    app = _make_app(operator_user, allow_operator=True, mock_device_config_service=mock_device_config_service)
    return TestClient(app)


@pytest.fixture
def client_no_operator(normal_user, mock_device_config_service):
    app = _make_app(normal_user, allow_operator=False, mock_device_config_service=mock_device_config_service)
    return TestClient(app)


class TestSetTemplateTypeProviderMap:
    """POST /api/v1/config/device/template-type-provider-map"""

    def test_success(self, client, mock_device_config_service):
        mapping = {"personalCoding": "baas", "applicationCoding": "baas"}
        resp = client.post(
            "/api/v1/config/device/template-type-provider-map",
            json={"mapping": mapping},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["mapping"] == mapping
        mock_device_config_service.set_template_type_provider_map.assert_called_once()
        call_kwargs = mock_device_config_service.set_template_type_provider_map.call_args.kwargs
        assert call_kwargs["mapping"] == mapping
        assert call_kwargs["creator"] == "test_user"

    def test_invalid_provider_returns_error(self, client, mock_device_config_service):
        mock_device_config_service.set_template_type_provider_map.side_effect = ValueError(
            "Invalid provider 'invalid' for template_type 'personalCoding'"
        )
        resp = client.post(
            "/api/v1/config/device/template-type-provider-map",
            json={"mapping": {"personalCoding": "invalid"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 40001
        assert "Invalid provider" in data["message"]

    def test_empty_mapping(self, client, mock_device_config_service):
        resp = client.post(
            "/api/v1/config/device/template-type-provider-map",
            json={"mapping": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["mapping"] == {}

    def test_non_operator_forbidden(self, client_no_operator):
        resp = client_no_operator.post(
            "/api/v1/config/device/template-type-provider-map",
            json={"mapping": {"personalCoding": "baas"}},
        )
        assert resp.status_code == 403


class TestSetPersonalBotBaasDisable:
    """POST /api/v1/config/device/personal-bot-baas-disable"""

    def test_disable(self, client, mock_device_config_service):
        resp = client.post(
            "/api/v1/config/device/personal-bot-baas-disable",
            json={"disabled": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["disabled"] is True
        mock_device_config_service.set_personal_bot_baas_disable.assert_called_once()
        call_kwargs = mock_device_config_service.set_personal_bot_baas_disable.call_args.kwargs
        assert call_kwargs["disabled"] is True
        assert call_kwargs["creator"] == "test_user"

    def test_enable(self, client, mock_device_config_service):
        resp = client.post(
            "/api/v1/config/device/personal-bot-baas-disable",
            json={"disabled": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["disabled"] is False
        call_kwargs = mock_device_config_service.set_personal_bot_baas_disable.call_args.kwargs
        assert call_kwargs["disabled"] is False

    def test_non_operator_forbidden(self, client_no_operator):
        resp = client_no_operator.post(
            "/api/v1/config/device/personal-bot-baas-disable",
            json={"disabled": True},
        )
        assert resp.status_code == 403
