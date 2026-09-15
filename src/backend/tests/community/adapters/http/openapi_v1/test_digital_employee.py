import time
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.api.digital_employee_service import DigitalEmployeeCatalogProtocol, DigitalEmployeeServiceProtocol
from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config

BASE = "/openapi/v1/bots/metadata/digital-employees"
KEY = "employee-catalog-test-signing-key-32-bytes"


@pytest.fixture
def client():
    resolver = Mock()
    resolver.get_secret.return_value = type("Secret", (), {"secret_value": KEY, "secret_user": "test"})()
    init_principal_verifier_config(resolver, "test-key", strict=False)
    service, catalog = Mock(), Mock()
    service.list_bindable.return_value = [{"agentId": "17", "creatorNo": "owner", "platform": "test"}]
    catalog.detail = AsyncMock(return_value={"agentId": "17", "agentCode": "code-17", "agentName": "Support", "creatorNo": "owner", "platform": "test", "agentPlatform": "test", "agentFramework": "openclaw", "modelList": [], "mcpDetailList": [], "skillList": [], "cliLis": [], "queriedVersionStatus": "published"})
    injector = Injector()
    injector.binder.bind(DigitalEmployeeServiceProtocol, to=service)
    injector.binder.bind(DigitalEmployeeCatalogProtocol, to=catalog)
    app = FastAPI()
    from agentclaw.community.adapters.http.app import _unhandled_exception_handler
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    attach_injector(app, injector)
    app.include_router(build_public_router())
    return TestClient(app, raise_server_exceptions=False), service, catalog


def headers():
    now = int(time.time())
    claims = {
        "iss": "gateway",
        "aud": "backend",
        "iat": now,
        "exp": now + 600,
        "principals": [{
            "type": "app",
            "tenant": "employee-test",
            "app": {
                "app_id": 17,
                "app_name": "employee-platform",
                "owners": "platform",
                "tenant": "employee-test",
                "app_type": "integration",
            },
        }],
    }
    return {"X-Avernet-Principal": jwt.encode(claims, KEY, algorithm="HS256")}


def test_open_catalog_still_requires_authenticated_principal(client):
    http, service, _ = client
    assert http.get(BASE, params={"creatorNo": "owner"}).status_code == 401
    service.list_bindable.assert_not_called()


def test_app_only_catalog_preserves_platform_contract(client):
    http, service, _ = client
    response = http.get(BASE, params={"creatorNo": "owner"}, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert response.json()["data"][0]["agentId"] == "17"
    service.list_bindable.assert_called_once_with("owner")


def test_detail_uses_metadata_primary_key_and_explicit_version(client):
    http, _, catalog = client
    response = http.get(BASE + "/17", params={"versionStatus": "published"}, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["data"]["agentCode"] == "code-17"
    catalog.detail.assert_awaited_once_with(17, "published")


def test_disabled_config_reports_failure_instead_of_empty_success(client):
    http, service, _ = client
    service.list_bindable.side_effect = DigitalEmployeeError("请补齐配置")
    response = http.get(BASE, params={"creatorNo": "owner"}, headers=headers())
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["errorMessage"] == "请补齐配置"
