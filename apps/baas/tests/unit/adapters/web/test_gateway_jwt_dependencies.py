"""JWT authentication distinguishes caller errors from secret-store failures."""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import Depends, FastAPI, Header
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.routers.gateway.dependencies import (
    validate_jwt_token,
)

_KEY = "test-signing-key-for-gateway-auth-at-least-32-bytes"


@pytest.fixture
def auth_client():
    secret = MagicMock()
    secret.get_secret.return_value = _KEY
    repository = MagicMock()
    repository.get_by_resource_key_and_tenant.return_value = MagicMock(
        id=7, tenant="test-tenant", app="test-app"
    )
    app = FastAPI()

    def authenticate(
        x_avernet_principal: str | None = Header(None, alias="x-avernet-principal"),
    ):
        return validate_jwt_token(
            x_avernet_principal=x_avernet_principal,
            secret_plugin=secret,
            resource_key_repository=repository,
            secret_name="test-secret",
        )

    app.dependency_overrides[validate_jwt_token] = authenticate

    @app.get("/auth")
    def authenticated(context=Depends(validate_jwt_token)):
        return {"tenant": context.tenant, "app": context.app}

    return TestClient(app), secret, repository


def test_missing_token_does_not_read_secret(auth_client):
    client, secret, repository = auth_client
    response = client.get("/auth")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == 40101
    secret.get_secret.assert_not_called()
    repository.get_by_resource_key_and_tenant.assert_not_called()


@pytest.mark.parametrize("token", ["not-a-jwt", "abc.def.ghi", "Bearer invalid"])
def test_malformed_token_is_unauthorized(auth_client, token):
    client, secret, repository = auth_client
    response = client.get("/auth", headers={"x-avernet-principal": token})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == 40103
    secret.get_secret.assert_called_once_with("test-secret")
    repository.get_by_resource_key_and_tenant.assert_not_called()


@pytest.mark.parametrize("failure", [RuntimeError, ValueError, TimeoutError])
def test_secret_failure_is_service_unavailable(auth_client, failure):
    client, secret, repository = auth_client
    secret.get_secret.side_effect = failure("private secret-store detail")
    response = client.get("/auth", headers={"x-avernet-principal": "not-a-jwt"})
    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": 50301, "message": "Authentication service unavailable"}
    }
    assert "private secret-store detail" not in response.text
    repository.get_by_resource_key_and_tenant.assert_not_called()


def test_empty_signing_secret_is_service_unavailable(auth_client):
    client, secret, repository = auth_client
    secret.get_secret.return_value = ""
    response = client.get("/auth", headers={"x-avernet-principal": "not-a-jwt"})
    assert response.status_code == 503
    repository.get_by_resource_key_and_tenant.assert_not_called()


@pytest.mark.parametrize("expired", [False, True])
def test_signed_token_keeps_authentication_semantics(auth_client, expired):
    client, secret, repository = auth_client
    token = jwt.encode(
        {
            "exp": datetime.now(UTC) + timedelta(seconds=-60 if expired else 300),
            "principals": [
                {"access_key": {"access_key": "test-resource"}, "tenant": "test-tenant"}
            ],
        },
        _KEY,
        algorithm="HS256",
    )
    response = client.get("/auth", headers={"x-avernet-principal": token})
    assert response.status_code == (401 if expired else 200)
    if expired:
        repository.get_by_resource_key_and_tenant.assert_not_called()
    else:
        assert response.json() == {"tenant": "test-tenant", "app": "test-app"}
        repository.get_by_resource_key_and_tenant.assert_called_once_with(
            "test-resource", "test-tenant"
        )


def test_every_chat_route_documents_authentication_service_unavailability():
    from secbaas.community.adapters.web.routers.gateway.message_router import (
        router as messages,
    )
    from secbaas.community.adapters.web.routers.gateway.session_router import (
        router as sessions,
    )

    app = FastAPI()
    app.include_router(messages)
    app.include_router(sessions)
    paths = app.openapi()["paths"]
    for method, path in [
        ("post", "/openapi/v1/chat"),
        ("post", "/openapi/v1/chat/stream"),
        ("get", "/openapi/v1/chat/messages/{message_id}"),
        ("get", "/openapi/v1/chat/sessions/{session_id}"),
        ("get", "/openapi/v1/chat/sessions/{session_id}/messages"),
    ]:
        assert "503" in paths[path][method]["responses"], path
