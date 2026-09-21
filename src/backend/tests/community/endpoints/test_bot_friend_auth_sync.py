"""Declarative endpoint coverage for the internal friend-auth sync route."""
from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_friend_auth_service import FriendAuthSyncServiceProtocol
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_PATH = "/api/internal/bot-friend-auth/sync"
_SIGNING_KEY = "bot-friend-auth-endpoint-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [{"type": "user", "subject": {"id": "85020", "username": "owner"}}],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )


init_principal_verifier_config(_Resolver(), "friend-auth-endpoint-test-key", strict=False)


class _SyncService:
    def sync(self, **_kwargs):
        return {"synced": True, "reason": "created", "auth_id": 42}


def _seed_sync_service(world) -> None:
    world.injector.binder.bind(
        FriendAuthSyncServiceProtocol,
        to=_SyncService(),
        scope=None,
    )


_BODY = {
    "bot_id": "bot-endpoint-1",
    "owner_work_no": "85020",
    "human_work_no": "88123",
    "action": "grant",
    "request_id": "endpoint-request-1",
}
_HEADERS = {PRINCIPAL_HEADER: _principal()}


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="happy_grant",
    seed=_seed_sync_service,
    input=CaseInput(headers=_HEADERS, json_body=_BODY),
    expect=ExpectSuccess(
        status=200,
        json_contains={"synced": True, "reason": "created", "auth_id": 42},
    ),
)
def sync_happy():
    pass


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="err_invalid_body",
    input=CaseInput(headers=_HEADERS, json_body={}),
    expect=ExpectError(status=422),
)
def sync_invalid_body():
    pass
