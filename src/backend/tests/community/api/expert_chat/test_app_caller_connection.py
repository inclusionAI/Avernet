"""Application Caller HTTP boundary with real Principal verification."""
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.expert_chat.router import router
from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.api.expert_chat_instance_service import ExpertChatInstanceServiceProtocol
from agentclaw.community.core.expert_chat.errors import ChatPermissionError
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config, reset_principal_verifier_config_cache

PATH = "/api/v1/expert-chats/app-caller-connection"
KEY = "application-boundary-test-key-at-least-32-bytes"
PARAMS = {"bot_id": "bot", "owner_id": "owner", "user_id": "caller"}


def token(kind="app", *, app_id=7, **overrides):
    principals = [{"type": "app", "tenant": "acme", "app": {"app_id": app_id, "app_name": "partner", "owners": "owner", "tenant": "acme"}}]
    if kind == "user":
        principals = []
    if kind in ("user", "mixed"):
        principals.append({"type": "user", "subject": {"id": "person", "username": "person", "tenant": "acme"}})
    claims = dict(iss="gateway", aud="baas", iat=int(time.time()), exp=int(time.time()) + 60, principals=principals)
    claims.update(overrides)
    return jwt.encode(claims, KEY, algorithm="HS256")


@pytest.fixture
def client_service():
    resolver = SimpleNamespace(get_secret=lambda _: SimpleNamespace(secret_value=KEY, secret_user="gateway"))
    init_principal_verifier_config(resolver, "test-key", strict=False)
    async def connect(**kwargs):
        assert get_current_avernet_tenant() == "acme"
        return {"instance": {"id": 19}, "connection": {"token": "response-secret", "nested": [{"Authorization": "nested-secret", "status": "active"}], "url": "wss://example.test/ws?token=url-secret&mode=chat"}, "need_poll": False}
    service = SimpleNamespace(get_application_caller_connection=AsyncMock(side_effect=connect))
    injector = Injector()
    def resolve_service():
        assert get_current_avernet_tenant() == "acme"
        return service
    injector.binder.bind(ExpertChatInstanceServiceProtocol, to=resolve_service)
    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.add_exception_handler(MissingPrincipalError, lambda _r, _e: JSONResponse({"detail": "Unauthorized"}, status_code=401))
    app.include_router(router)
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield client, service
    reset_principal_verifier_config_cache()


@pytest.mark.parametrize("kind,app_id", [("app", 7), ("app", 8), ("mixed", 7)])
def test_application_success_without_user_cookie(client_service, caplog, kind, app_id):
    client, service = client_service
    caplog.set_level(logging.INFO)
    credential = token(kind, app_id=app_id)
    response = client.post(PATH, params=PARAMS, headers={"X-Avernet-Principal": credential})
    assert response.status_code == 200
    assert response.json()["data"]["connection"]["token"] == "response-secret"
    assert response.json()["success"] is True
    service.get_application_caller_connection.assert_awaited_once_with(app_id=app_id, tenant="acme", force_upgrade=False, **PARAMS)
    assert get_current_avernet_tenant() == "teamclaw"
    assert "app_caller_connection.request" in caplog.text
    assert "app_caller_connection.success" in caplog.text
    assert "active" in caplog.text and "19" in caplog.text
    for secret in (credential, "response-secret", "nested-secret", "url-secret"):
        assert secret not in caplog.text


@pytest.mark.parametrize("credential", [None, "forged", "user", "expired", "issuer"])
def test_authentication_denied_before_service(client_service, credential):
    client, service = client_service
    values = {"user": token("user"), "expired": token(exp=1), "issuer": token(iss="other")}
    headers = {} if credential is None else {"X-Avernet-Principal": values.get(credential, credential)}
    response = client.post(PATH, params=PARAMS, headers=headers)
    assert response.status_code == 401
    service.get_application_caller_connection.assert_not_called()


@pytest.mark.parametrize("error,code,event", [(ChatPermissionError("exception-secret"), 403, "denied"), (RuntimeError("exception-secret"), 5999, "failed")])
def test_failure_mapping_and_safe_logs(client_service, caplog, error, code, event):
    client, service = client_service
    caplog.set_level(logging.INFO)
    service.get_application_caller_connection.side_effect = error
    response = client.post(PATH, params={**PARAMS, "force_upgrade": "true"}, headers={"X-Avernet-Principal": token()})
    assert response.json()["error_code"] == code
    assert response.json()["success"] is False
    assert f"app_caller_connection.{event}" in caplog.text
    assert "exception-secret" not in caplog.text
    assert get_current_avernet_tenant() == "teamclaw"


@pytest.mark.parametrize("params", [{"owner_id": "owner", "user_id": "caller"}, {**PARAMS, "force_upgrade": "invalid"}])
def test_invalid_parameters(client_service, params):
    client, service = client_service
    assert client.post(PATH, params=params, headers={"X-Avernet-Principal": token()}).status_code == 422
    service.get_application_caller_connection.assert_not_called()


def test_invalid_principal_payload_never_logs_credentials(client_service, caplog):
    client, service = client_service
    credential = token(principals=[{"type": "app", "tenant": "acme", "app": {"app_id": "malformed-payload-secret", "app_name": "app", "owners": "owner", "tenant": "acme"}}])
    assert client.post(PATH, params=PARAMS, headers={"X-Avernet-Principal": credential}).status_code == 401
    service.get_application_caller_connection.assert_not_called()
    assert "malformed-payload-secret" not in caplog.text


def test_redaction_handles_nested_urls_and_binary():
    from agentclaw.community.adapters.http.expert_chat.router import _application_log_value
    result = _application_log_value({
        "url": "https://user:password-secret@example.test/ws?redirect=https%3A%2F%2Fexample.test%2F%3Ftoken%3Dnested-url-secret&mode=chat",
        "blob": b"binary-secret",
        "nested": [{"privateKey": "private-secret", "session": "session-secret", "ok": 17}],
    })
    rendered = str(result)
    for secret in ("password-secret", "nested-url-secret", "binary-secret", "private-secret", "session-secret"):
        assert secret not in rendered
    assert "mode=chat" in rendered and "17" in rendered


def test_verifier_cached_and_tenant_reset(client_service, monkeypatch):
    from agentclaw.community.adapters.http.org import dependencies
    client, service = client_service
    calls = []
    original = dependencies.verify_principal_token
    def verify(raw, config):
        calls.append(config.verify_audience)
        return original(raw, config)
    monkeypatch.setattr(dependencies, "verify_principal_token", verify)
    assert client.post(PATH, params=PARAMS, headers={"X-Avernet-Principal": token()}).json()["success"] is True
    assert calls == [False]
    assert get_current_avernet_tenant() == "teamclaw"
    assert client.post(PATH, params=PARAMS).status_code == 401
    assert get_current_avernet_tenant() == "teamclaw"
    assert service.get_application_caller_connection.await_count == 1


def test_contradictory_tenant_fails_closed(client_service):
    client, service = client_service
    principals = [{"type": "app", "tenant": "acme", "app": {"app_id": 7, "app_name": "partner", "owners": "owner", "tenant": "other"}}]
    principals.append({"type": "app", "tenant": "other", "app": {"app_id": 8, "app_name": "second", "owners": "owner", "tenant": "other"}})
    assert client.post(PATH, params=PARAMS, headers={"X-Avernet-Principal": token(principals=principals)}).status_code == 401
    service.get_application_caller_connection.assert_not_called()
