"""Tests for the ordinary JWT-authenticated current-user endpoint."""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.adapters.http.org_user.router import router
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)

_PATH = "/api/v1/org/user"
_USER_ID = "org-user-1"
_SIGNING_KEY = "org-user-router-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _token(*, user: bool = True, app: bool = False, key: str = _SIGNING_KEY) -> str:
    now = int(time.time())
    principals: list[dict[str, object]] = []
    if user:
        principals.append(
            {
                "type": "user",
                "subject": {
                    "id": _USER_ID,
                    "username": "org-user@example.test",
                    "display_name": "Org User",
                    "full_name": "Organization User",
                    "tenant_id": "identity-provider-metadata",
                },
            }
        )
    if app:
        principals.append(
            {
                "type": "app",
                "tenant": "teamclaw",
                "app": {
                    "app_id": 8,
                    "app_name": "ordinary-http-client",
                    "owners": "platform",
                    "tenant": "teamclaw",
                },
            }
        )
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": principals,
        },
        key,
        algorithm="HS256",
    )


@pytest.fixture
def client():
    from agentclaw.community.adapters.http.app import _principal_error_handler

    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(MissingPrincipalError, _principal_error_handler)
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    yield TestClient(app, raise_server_exceptions=False)
    reset_principal_verifier_config_cache()


def test_returns_only_the_verified_user_profile(client: TestClient) -> None:
    response = client.get(_PATH, headers={PRINCIPAL_HEADER: _token()})

    assert response.status_code == 200
    assert response.json() == {
        "user_id": _USER_ID,
        "username": "org-user@example.test",
        "display_name": "Org User",
        "full_name": "Organization User",
    }
    assert "tenant" not in response.json()


def test_user_and_app_principal_still_returns_the_user(client: TestClient) -> None:
    response = client.get(_PATH, headers={PRINCIPAL_HEADER: _token(app=True)})

    assert response.status_code == 200
    assert response.json()["user_id"] == _USER_ID


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {PRINCIPAL_HEADER: "not-a-jwt"},
        {PRINCIPAL_HEADER: _token(user=False, app=True)},
        {PRINCIPAL_HEADER: _token(key="different-signing-key-at-least-32-bytes")},
    ],
)
def test_requires_a_verified_user_principal(
    client: TestClient,
    headers: dict[str, str],
) -> None:
    response = client.get(_PATH, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


def _walk_routes(routes):
    for route in routes:
        yield route
        original = getattr(route, "original_router", None)
        nested = (
            getattr(original, "routes", None)
            if original is not None
            else getattr(route, "routes", None)
        )
        if nested:
            yield from _walk_routes(nested)


def test_full_application_imports_and_mounts_the_route() -> None:
    from agentclaw.community.adapters.http.app import app

    routes = {
        (method, route.path)
        for route in _walk_routes(app.routes)
        for method in getattr(route, "methods", set())
    }
    assert ("GET", _PATH) in routes
