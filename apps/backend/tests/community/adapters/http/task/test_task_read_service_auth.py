"""Task reads accept verified internal apps, never unsigned identity hints."""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.plugin_api.auth import AuthenticatedIdentity, AuthPlugin
from agentclaw.community.utils.gateway_principal_config import (
    reset_principal_verifier_config_cache,
)
from tests.community.adapters.http.openapi_v1.test_principal_seam import (
    KEY,
    boot_with_key,
    mint,
)
from tests.community.adapters.http.task.test_task_internal_dashboard_list import (
    _StubArtifactRepo,
    _StubTaskService,
    _client,
)

PATHS = [
    "/api/v1/collaboration/tasks/dashboard?task_id=t1",
    "/api/v1/collaboration/tasks/t1",
    "/api/v1/collaboration/tasks/artifacts?task_id=t1",
    "/api/v1/collaboration/tasks/list?user_id=owner-1",
]


class CookieAuth:
    def __init__(self):
        self.calls = 0

    async def resolve_user_from_request(self, ctx):
        self.calls += 1
        if ctx.cookies.get("session") != "test-session":
            raise HTTPException(status_code=401, detail="missing login cookie")
        return AuthenticatedIdentity(
            id="owner-1", staffId="owner-1", operatorName="owner"
        )


@pytest.fixture
def client():
    boot_with_key(KEY)
    auth = CookieAuth()
    client = _client(_StubTaskService(), _StubArtifactRepo([]))
    client.app.dependency_overrides.pop(get_current_user, None)
    from agentclaw.community.adapters.http.task.read_auth import task_read_actor

    client.app.dependency_overrides.pop(task_read_actor, None)
    client.app.state.injector.binder.bind(AuthPlugin, to=auth)
    from agentclaw.community.core.errors import DomainError
    from fastapi.responses import JSONResponse

    @client.app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=302, content={"detail": str(exc)})

    yield client, auth
    reset_principal_verifier_config_cache()


@pytest.mark.parametrize("path", PATHS)
def test_verified_internal_app_can_read_without_cookie(client, path):
    http, auth = client
    token = mint(tenant="teamclaw", user_id=None)
    response = http.get(path, headers={"X-Avernet-Principal": token})
    assert response.status_code == 200, response.text
    assert auth.calls == 0


@pytest.mark.parametrize("path", PATHS)
def test_user_cookie_still_works(client, path):
    http, auth = client
    response = http.get(path, headers={"Cookie": "session=test-session"})
    assert response.status_code == 200, response.text
    assert auth.calls == 1


@pytest.mark.parametrize(
    "kind", ["forged", "expired", "audience", "issuer", "external", "unsigned"]
)
def test_unverified_or_non_internal_service_cannot_read(client, kind):
    http, auth = client
    if kind == "unsigned":
        token = "not-a-token"
    elif kind == "external":
        token = mint(tenant="external-tenant", user_id=None)
    elif kind == "user_only":
        token = mint(tenant=None)
    elif kind == "expired":
        claims = jwt.decode(
            mint(tenant="teamclaw", user_id=None), options={"verify_signature": False}
        )
        claims["iat"] = int(time.time()) - 120
        claims["exp"] = int(time.time()) - 60
        token = jwt.encode(claims, KEY, algorithm="HS256")
    else:
        overrides = {
            "forged": {"key": "wrong-signing-key-at-least-32-bytes"},
            "audience": {"audience": "baas"},
            "issuer": {"issuer": "unknown"},
        }[kind]
        token = mint(tenant="teamclaw", user_id=None, **overrides)
    response = http.get(
        PATHS[0],
        headers={"X-Avernet-Principal": token, "Cookie": "session=test-session"},
    )
    assert response.status_code == 401, response.text
    assert auth.calls == 0  # No downgrade to a valid cookie after a bad principal.


def test_bare_app_name_does_not_authenticate(client):
    http, _ = client
    response = http.get(PATHS[0], headers={"X-App-Name": "ecb"})
    assert response.status_code != 200


@pytest.mark.parametrize("path", PATHS)
def test_forwarded_browser_user_keeps_cookie_authentication(client, path):
    http, auth = client
    response = http.get(
        path,
        headers={
            "X-Avernet-Principal": mint(tenant=None),
            "Cookie": "session=test-session",
        },
    )
    assert response.status_code == 200
    assert auth.calls == 1


def test_forwarded_user_without_cookie_is_not_a_service(client):
    http, auth = client
    response = http.get(PATHS[0], headers={"X-Avernet-Principal": mint(tenant=None)})
    assert response.status_code != 200
    assert auth.calls == 1


def test_service_principal_does_not_bypass_user_write_auth(client):
    http, auth = client
    response = http.post(
        "/api/v1/collaboration/tasks/grant",
        headers={"X-Avernet-Principal": mint(tenant="teamclaw", user_id=None)},
        json={"bcs_bot_id": "bot-1:owner-1"},
    )
    assert response.status_code != 200
    assert auth.calls == 1


def test_missing_verifier_key_does_not_admit_service(client):
    http, auth = client
    boot_with_key(None)
    response = http.get(
        PATHS[0], headers={"X-Avernet-Principal": mint(tenant="teamclaw", user_id=None)}
    )
    assert response.status_code == 401
    assert auth.calls == 0
