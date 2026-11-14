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
async def test_dingtalk_authenticates_application_before_accepting_user(monkeypatch):
    service = _service()
    authenticate = AsyncMock()
    monkeypatch.setattr(importlib.import_module("agentclaw.community.adapters.http.token_exchange.router"), "require_baas_caller", authenticate)
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "dingtalk", "user_id": "caller", "owner_id": "owner", "bot_id": "bot"}, headers={"Cookie": "IAM_TOKEN=device-secret"})
    assert response.status_code == 200
    authenticate.assert_awaited_once()
    kwargs = service.get_iam_token.call_args.kwargs
    assert kwargs["token_type"] == "dingtalk"
    assert kwargs["expected_user_id"] == "caller"
    assert kwargs["expected_owner_id"] == "owner"
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
