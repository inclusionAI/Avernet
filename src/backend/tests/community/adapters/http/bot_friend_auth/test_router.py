"""Router tests for POST /api/internal/bot-friend-auth/sync.

Mirrors work_orders/test_router.py: attach_injector with a MagicMock bound
to FriendAuthSyncServiceProtocol; sign a real principal JWT and exercise
200 / 400 / 401 / 502 paths. Success body is the bare schema (no Envelope);
errors are wrapped by app.py global handlers into Envelope.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentclaw.community.adapters.http.bot_friend_auth.router import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_friend_auth_service import FriendAuthSyncServiceProtocol
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)

_PATH = "/api/internal/bot-friend-auth/sync"
_USER_ID = "85020"
_SIGNING_KEY = "bot-friend-auth-router-signing-key-at-least-32-bytes-long"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal_token() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [{"type": "user", "subject": {"id": _USER_ID, "username": "owner"}}],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )


def _principal_headers() -> dict[str, str]:
    return {PRINCIPAL_HEADER: _principal_token()}


def _payload(action: str = "grant") -> dict[str, object]:
    return {
        "bot_id": "bot-1",
        "owner_work_no": "85020",
        "human_work_no": "88123",
        "action": action,
        "request_id": "req-1",
    }


@pytest.fixture
def service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def app(service: MagicMock):
    class _Bindings(Module):
        def configure(self, binder) -> None:
            binder.bind(FriendAuthSyncServiceProtocol, to=service)

    test_app = FastAPI()
    test_app.include_router(router)
    attach_injector(test_app, Injector([_Bindings()]))

    from agentclaw.community.adapters.http.app import (
        _domain_error_handler,
        _http_exception_handler,
        _principal_error_handler,
        _unhandled_exception_handler,
        _validation_error_handler,
    )

    test_app.add_exception_handler(Exception, _unhandled_exception_handler)
    test_app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    test_app.add_exception_handler(RequestValidationError, _validation_error_handler)
    from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
    test_app.add_exception_handler(MissingPrincipalError, _principal_error_handler)

    @test_app.middleware("http")
    async def _trace(request: Request, call_next):
        request.state.trace_id = "trace-fa"
        return await call_next(request)

    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    yield test_app
    reset_principal_verifier_config_cache()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update(_principal_headers())
    return client


def test_grant_returns_200_bare_schema(client, service):
    service.sync.return_value = {"synced": True, "reason": "created", "auth_id": 42}
    resp = client.post(_PATH, json=_payload("grant"))
    assert resp.status_code == 200
    assert resp.json() == {"synced": True, "reason": "created", "auth_id": 42}
    service.sync.assert_called_once_with(
        bot_id="bot-1", owner_work_no="85020", human_work_no="88123", action="grant"
    )


def test_revoke_returns_200_not_found_idempotent(client, service):
    service.sync.return_value = {"synced": True, "reason": "not_found", "auth_id": None}
    resp = client.post(_PATH, json=_payload("revoke"))
    assert resp.status_code == 200
    assert resp.json()["reason"] == "not_found"


def test_bot_not_found_maps_to_400_envelope(client, service):
    from agentclaw.community.core.bot_public.services.friend_auth_sync_service import (
        BotNotFoundError,
    )
    service.sync.side_effect = BotNotFoundError("bot-1")
    resp = client.post(_PATH, json=_payload("grant"))
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400000
    assert body["data"] is None


def test_agent_code_unavailable_maps_to_400(client, service):
    from agentclaw.community.core.bot_public.services.friend_auth_sync_service import (
        AgentCodeUnavailableError,
    )
    service.sync.side_effect = AgentCodeUnavailableError("bot-1")
    resp = client.post(_PATH, json=_payload("grant"))
    assert resp.status_code == 400


def test_auth_rel_failure_maps_to_502(client, service):
    from agentclaw.community.core.bot_public.services.friend_auth_sync_service import (
        AuthRelationshipSyncError,
    )
    service.sync.side_effect = AuthRelationshipSyncError("boom")
    resp = client.post(_PATH, json=_payload("grant"))
    assert resp.status_code == 502


def test_request_without_principal_is_401(app, service):
    resp = TestClient(app, raise_server_exceptions=False).post(_PATH, json=_payload("grant"))
    assert resp.status_code == 401
    service.sync.assert_not_called()


def test_bad_principal_is_401(app, service):
    resp = TestClient(app, raise_server_exceptions=False).post(
        _PATH, json=_payload("grant"), headers={PRINCIPAL_HEADER: "not-a-jwt"}
    )
    assert resp.status_code == 401


def test_missing_required_field_is_422(client, service):
    resp = client.post(_PATH, json={"bot_id": "bot-1", "owner_work_no": "85020"})
    assert resp.status_code == 422
    service.sync.assert_not_called()
