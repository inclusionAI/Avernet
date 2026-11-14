import inspect
import importlib
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentclaw.community.adapters.http.token_exchange.router import get_iam_token, router
from tests.community.api.test_caller_iam_token import _service


def _app(service):
    app = FastAPI()
    app.include_router(router)
    dependency = inspect.signature(get_iam_token).parameters["service"].default.dependency
    app.dependency_overrides[dependency] = lambda: service
    return app


@pytest.mark.asyncio
async def test_unknown_source_rejected_without_exchange():
    service = _service()
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "unknown"})
    assert response.status_code == 422
    service.get_iam_token.assert_not_called()


@pytest.mark.asyncio
async def test_dingtalk_uses_existing_locators_and_ignores_extra_identity_queries(monkeypatch):
    service = _service()
    authenticate = AsyncMock()
    monkeypatch.setattr(importlib.import_module("agentclaw.community.adapters.http.token_exchange.router"), "require_baas_caller", authenticate)
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "dingtalk", "user_id": "caller", "owner_id": "owner", "bot_id": "bot"}, headers={"Cookie": "IAM_TOKEN=device-secret"})
    assert response.status_code == 200
    authenticate.assert_awaited_once()
    kwargs = service.get_iam_token.call_args.kwargs
    assert kwargs["token_type"] == "dingtalk"
    assert "expected_user_id" not in kwargs
    assert "expected_owner_id" not in kwargs
    assert "user_id" not in kwargs
    assert "owner_id" not in kwargs
    assert kwargs["iam_token"] == "device-secret"


@pytest.mark.asyncio
async def test_device_missing_signed_principal_cannot_reach_service(monkeypatch):
    from fastapi import HTTPException
    service = _service()
    authenticate = AsyncMock(side_effect=HTTPException(status_code=401))
    monkeypatch.setattr(importlib.import_module("agentclaw.community.adapters.http.token_exchange.router"), "require_baas_caller", authenticate)
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "dingtalk", "user_id": "caller", "owner_id": "owner"})
    assert response.status_code == 401
    service.get_iam_token.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("principal_kind", ["valid", "missing", "wrong-key", "expired", "wrong-issuer"])
async def test_device_requires_real_baas_signature(monkeypatch, principal_kind, caplog):
    import secrets
    import time
    import jwt
    from fastapi.responses import JSONResponse
    from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
    from agentclaw.community.core.gateway_principal import PrincipalVerifierConfig

    key = secrets.token_hex(32)
    config = PrincipalVerifierConfig(signing_key=key, issuer="gateway", audience="backend")
    dependencies = importlib.import_module("agentclaw.community.adapters.http.org.dependencies")
    monkeypatch.setattr(dependencies, "get_principal_verifier_config", lambda: config)
    service = _service()
    app = _app(service)

    @app.exception_handler(MissingPrincipalError)
    async def missing_principal(_request, _error):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    now = int(time.time())
    claims = {"iss": "baas" if principal_kind != "wrong-issuer" else "gateway", "iat": now - 120,
              "exp": now + 300 if principal_kind != "expired" else now - 60}
    principal = jwt.encode(claims, secrets.token_hex(32) if principal_kind == "wrong-key" else key, algorithm="HS256")
    headers = {"Cookie": "IAM_TOKEN=device-secret"}
    if principal_kind != "missing":
        headers["X-Avernet-Principal"] = principal
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "dingtalk", "user_id": "caller", "owner_id": "owner", "bot_id": "bot"}, headers=headers)
    assert response.status_code == (200 if principal_kind == "valid" else 401)
    assert service.get_iam_token.call_count == (1 if principal_kind == "valid" else 0)
    assert principal not in caplog.text
    assert "device-secret" not in caplog.text


def test_iam_schema_adds_only_source_selector():
    app = _app(_service())
    parameters = app.openapi()["paths"]["/api/v1/token/iam"]["get"]["parameters"]
    names = {item["name"] for item in parameters}
    assert names == {"bot_id", "stage", "publish_id", "entity_id", "is_test_exchange", "session_key", "sessionKey", "token_type"}
    assert not any(item.get("required") for item in parameters)


@pytest.mark.asyncio
async def test_extra_query_identity_cannot_change_verified_caller_target(monkeypatch):
    from dataclasses import replace
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from tests.community.core.caller_identity.test_iam_token_service import _service as core_service, _caller_token
    from agentclaw.community.core.runtime_binding.models import ResolvedRuntimeBinding, RuntimeBindingSource

    service, identity, auth, provider, _, bindings, _ = core_service()
    service._token_provider = SimpleNamespace(exchange_dingtalk=provider.exchange_dingtalk)
    provider.exchange_dingtalk.return_value = replace(_caller_token(), subject_user_id="007654", expires_at=datetime.now() + timedelta(hours=1))
    bindings.resolve.side_effect = None
    bindings.resolve.return_value = ResolvedRuntimeBinding(41, RuntimeBindingSource.CALLER_INSTANCE)
    monkeypatch.setattr(importlib.import_module("agentclaw.community.adapters.http.token_exchange.router"), "require_baas_caller", AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "dingtalk", "bot_id": "bot-1", "entity_id": "entity-1", "user_id": "attacker-selected-user", "owner_id": "attacker-selected-owner"}, headers={"Cookie": "IAM_TOKEN=device-secret"})
    assert response.status_code == 200
    assert provider.exchange_dingtalk.call_args.kwargs["owner_user_id"] == "owner-1"
    assert bindings.resolve.call_args.args[0].actor_user_id == "007654"
    assert bindings.resolve.call_args.args[0].owner_id == "owner-1"
    installed = identity.exchange_caller_identity.call_args.kwargs
    assert installed["caller_user_id"] == "007654"
    assert installed["owner_user_id"] == "owner-1"
    assert installed["entity_id"] == "entity-1"
    auth.resolve_user_from_request.assert_not_called()
