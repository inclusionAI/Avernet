import inspect
import logging

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
async def test_dingtalk_uses_existing_locators_and_ignores_extra_identity_queries():
    service = _service()
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "dingtalk", "user_id": "caller", "owner_id": "owner", "bot_id": "bot"}, headers={"Cookie": "IAM_TOKEN=device-secret"})
    assert response.status_code == 200
    kwargs = service.get_iam_token.call_args.kwargs
    assert kwargs["token_type"] == "dingtalk"
    assert "expected_user_id" not in kwargs
    assert "expected_owner_id" not in kwargs
    assert "user_id" not in kwargs
    assert "owner_id" not in kwargs
    assert kwargs["iam_token"] == "device-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", [None, "untrusted-principal-secret"])
async def test_device_exchange_does_not_require_app_principal(principal, caplog):
    caplog.set_level(logging.INFO)
    service = _service(iam_token="device-secret")
    headers = {"Cookie": "IAM_TOKEN=device-secret", "Authorization": "Bearer authorization-secret"}
    if principal is not None:
        headers["X-Avernet-Principal"] = principal
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "dingtalk", "bot_id": "bot", "entity_id": "entity", "stage": "draft", "session_key": "caller-session"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"success": True, "iam_token": "device-secret"}
    service.get_iam_token.assert_awaited_once()
    kwargs = service.get_iam_token.call_args.kwargs
    assert kwargs["iam_token"] == "device-secret"
    assert kwargs["token_type"] == "dingtalk"
    assert kwargs["entity_id"] == "entity"
    assert kwargs["session_key"] == "caller-session"
    assert kwargs["auth_request"].headers["authorization"] == "Bearer authorization-secret"
    assert "caller_iam_request_received" in caplog.text
    assert "caller_iam_response_succeeded" in caplog.text
    assert "caller-session" in caplog.text
    for secret in ("device-secret", "authorization-secret", "untrusted-principal-secret"):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_device_missing_cookie_is_rejected_by_service_without_exchange(caplog):
    from tests.community.core.caller_identity.test_iam_token_service import _service as core_service

    caplog.set_level(logging.INFO)
    service, identity, _, provider, _, _, _ = core_service()
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.get("/api/v1/token/iam", params={"token_type": "dingtalk", "bot_id": "bot-1"})
    assert response.status_code == 400
    assert response.json() == {"success": False, "error": "CALLER_CREDENTIAL_REQUEST_INVALID"}
    provider.exchange_dingtalk.assert_not_called()
    identity.exchange_caller_identity.assert_not_called()
    assert "caller_dingtalk_failed" in caplog.text
    assert "caller_iam_response_failed" in caplog.text


def test_iam_schema_adds_only_source_selector():
    app = _app(_service())
    parameters = app.openapi()["paths"]["/api/v1/token/iam"]["get"]["parameters"]
    names = {item["name"] for item in parameters}
    assert names == {"bot_id", "stage", "publish_id", "entity_id", "is_test_exchange", "session_key", "sessionKey", "token_type"}
    assert not any(item.get("required") for item in parameters)


@pytest.mark.asyncio
async def test_extra_query_identity_cannot_change_verified_caller_target():
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
