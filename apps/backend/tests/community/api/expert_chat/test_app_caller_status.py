"""Read-only BaaS Caller status with real signed service credentials."""
import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.expert_chat.router import router
from agentclaw.community.adapters.http.openapi_v1.bots.router import router as public_router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.api.expert_chat_instance_service import ExpertChatInstanceServiceProtocol
from agentclaw.community.core.expert_chat.errors import ChatPermissionError
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config, reset_principal_verifier_config_cache

PATH = "/api/v1/expert-chats/app-caller-instance/status"
KEY = "application-boundary-test-key-at-least-32-bytes"
PARAMS = {"bot_id": "bot", "owner_id": "owner", "user_id": "caller"}


def token(*, key=KEY, algorithm="HS256", omit=(), **overrides):
    claims = dict(iss="baas", iat=int(time.time()), exp=int(time.time()) + 60)
    claims.update(overrides)
    for name in omit:
        claims.pop(name, None)
    return jwt.api_jws.encode(json.dumps(claims).encode(), key, algorithm=algorithm)


@pytest.fixture
def client_service():
    resolver = SimpleNamespace(get_secret=lambda _: SimpleNamespace(secret_value=KEY, secret_user="gateway"))
    init_principal_verifier_config(resolver, "test-key", strict=False)
    async def connect(**kwargs):
        assert get_current_avernet_tenant() == "teamclaw"
        return {"exists": False, "alive": False}
    service = SimpleNamespace(get_caller_instance_status=AsyncMock(side_effect=connect))
    injector = Injector()
    def resolve_service():
        assert get_current_avernet_tenant() == "teamclaw"
        return service
    injector.binder.bind(ExpertChatInstanceServiceProtocol, to=resolve_service)
    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.add_exception_handler(MissingPrincipalError, lambda _r, _e: JSONResponse({"detail": "Unauthorized"}, status_code=401))
    app.include_router(router)
    app.include_router(public_router, dependencies=[Depends(require_principal)])
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield client, service
    reset_principal_verifier_config_cache()


@pytest.mark.parametrize("claims", [{}, *[
    {"principals": value, "app_id": value, "tenant": value, "aud": value}
    for value in (None, [], "other-tenant-secret", 17, {"malformed": ["payload-secret"]})
], {"nbf": int(time.time()) - 1}, {"iat": int(time.time()) + 2}])
def test_application_success_without_user_cookie(client_service, caplog, claims):
    client, service = client_service
    caplog.set_level(logging.INFO)
    encoded_principal = token(**claims)
    response = client.get(PATH, params=PARAMS, headers={"X-Avernet-Principal": encoded_principal})
    assert response.status_code == 200
    assert response.json()["data"] == {"exists": False, "alive": False}
    assert response.json()["success"] is True
    service.get_caller_instance_status.assert_awaited_once_with(**PARAMS)
    assert get_current_avernet_tenant() == "teamclaw"
    assert "app_caller_instance_status.request" in caplog.text
    assert "app_caller_instance_status.success" in caplog.text
    assert "exists" in caplog.text and "alive" in caplog.text
    assert "app_caller_instance_status.authenticated" in caplog.text
    assert "teamclaw" in caplog.text and "env" in caplog.text
    for secret in (encoded_principal, "response-secret", "nested-secret", "url-secret", KEY, "payload-secret", "other-tenant-secret"):
        assert secret not in caplog.text


@pytest.mark.parametrize("case", ["missing", "empty", "malformed", "bearer", "key", "HS512", "none", "tamper", "gateway", "bcs", "issuer_type", "missing_iss", "missing_iat", "missing_exp", "expired", "future_iat", "future_nbf"])
def test_authentication_denied_before_service(client_service, caplog, case):
    client, service = client_service
    caplog.set_level(logging.INFO)
    encoded_principal = {
        "missing": None, "empty": "", "malformed": "secret-malformed", "bearer": "Bearer " + token(),
        "key": token(key="wrong-secret-key-with-at-least-32-bytes"),
        "HS512": token(key=KEY * 2, algorithm="HS512"), "none": token(key="", algorithm="none"),
        "tamper": token()[:-8] + "AAAAAAAA", "gateway": token(iss="gateway"),
        "bcs": token(iss="bcs"), "issuer_type": token(iss=["baas"]),
        "missing_iss": token(omit=("iss",)), "missing_iat": token(omit=("iat",)),
        "missing_exp": token(omit=("exp",)), "expired": token(exp=1),
        "future_iat": token(iat=int(time.time()) + 60),
        "future_nbf": token(nbf=int(time.time()) + 60),
    }[case]
    headers = {} if encoded_principal is None else {"X-Avernet-Principal": encoded_principal}
    response = client.get(PATH, params=PARAMS, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    service.get_caller_instance_status.assert_not_called()
    assert "app_caller_instance_status.denied" in caplog.text
    assert KEY not in caplog.text
    if encoded_principal:
        assert encoded_principal not in caplog.text


@pytest.mark.parametrize("error,code,event", [(ChatPermissionError("exception-secret"), 403, "denied"), (RuntimeError("exception-secret"), 5999, "failed")])
def test_failure_mapping_and_safe_logs(client_service, caplog, error, code, event):
    client, service = client_service
    caplog.set_level(logging.INFO)
    service.get_caller_instance_status.side_effect = error
    response = client.get(PATH, params=PARAMS, headers={"X-Avernet-Principal": token()})
    assert response.json()["error_code"] == code
    assert response.json()["success"] is False
    assert f"app_caller_instance_status.{event}" in caplog.text
    assert "exception-secret" not in caplog.text
    assert get_current_avernet_tenant() == "teamclaw"


@pytest.mark.parametrize("name", list(PARAMS))
@pytest.mark.parametrize("value", [None, ""])
def test_required_parameters(client_service, name, value):
    client, service = client_service
    params = dict(PARAMS)
    if value is None:
        params.pop(name)
    else:
        params[name] = value
    assert client.get(PATH, params=params, headers={"X-Avernet-Principal": token()}).status_code == 422
    service.get_caller_instance_status.assert_not_called()


def test_diagnostic_operation_is_not_a_query_parameter(client_service):
    client, _service = client_service
    operation = client.get("/openapi.json").json()["paths"][PATH]["get"]
    assert {p["name"] for p in operation["parameters"]} == set(PARAMS)


def test_verifier_uses_endpoint_config_and_tenant_reset(client_service, monkeypatch):
    from agentclaw.community.adapters.http.org import dependencies
    client, service = client_service
    calls = []
    original = dependencies.decode_principal_token
    shared = dependencies.get_principal_verifier_config()
    def verify(raw, config):
        calls.append((config.verify_audience, config.issuer))
        return original(raw, config)
    monkeypatch.setattr(dependencies, "decode_principal_token", verify)
    assert client.get(PATH, params={**PARAMS, "tenant": "foreign"}, headers={"X-Avernet-Principal": token(tenant="foreign")}).json()["success"] is True
    assert calls == [(False, "baas")]
    assert dependencies.get_principal_verifier_config() is shared
    assert shared.issuer == "gateway" and shared.verify_audience
    assert get_current_avernet_tenant() == "teamclaw"
    assert client.get(PATH, params=PARAMS).status_code == 401
    assert get_current_avernet_tenant() == "teamclaw"
    assert service.get_caller_instance_status.await_count == 1


def test_unconfigured_key_fails_closed(client_service):
    client, service = client_service
    reset_principal_verifier_config_cache()
    assert client.get(PATH, params=PARAMS, headers={"X-Avernet-Principal": token()}).status_code == 401
    service.get_caller_instance_status.assert_not_called()


def test_decoder_exception_does_not_log_untrusted_details(client_service, monkeypatch, caplog):
    from agentclaw.community.adapters.http.org import dependencies
    from agentclaw.community.core.gateway_principal import PrincipalVerificationError
    client, service = client_service
    caplog.set_level(logging.INFO)
    def reject(raw, config):
        raise PrincipalVerificationError("attacker-jose-secret")
    monkeypatch.setattr(dependencies, "decode_principal_token", reject)
    response = client.get(PATH, params=PARAMS, headers={"X-Avernet-Principal": token()})
    assert response.status_code == 401
    assert "PrincipalVerificationError" in caplog.text
    assert "attacker-jose-secret" not in caplog.text
    service.get_caller_instance_status.assert_not_called()


def test_service_jwt_does_not_authorize_the_public_status_endpoint(client_service):
    client, service = client_service
    response = client.get(
        "/openapi/v1/bots/bot/caller-instance/status",
        params={"entity_id": "owner", "user_id": "caller"},
        headers={"X-Avernet-Principal": token()},
    )
    assert response.status_code == 401
    service.get_caller_instance_status.assert_not_called()
