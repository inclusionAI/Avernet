"""Endpoint-framework coverage for the OpenAPI session-favorites surface."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.di_seams import bind_overrides


_USER_ID = "session-favorites-user"
_BOT_ID = "session-favorites-bot"
_SESSION_ID = "session:favorite:user:1"
_KEY = "session-favorites-framework-signing-key-at-least-32-bytes"
_BASE = "/openapi/v1/bots/{bot_id}/sessions"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


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
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": _USER_ID,
                        "username": "session-favorites@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _USER_ID}


async def _resolve_bot(_self, bot_id: str, owner_id: str, caller_id: str) -> BotFacts:
    assert (bot_id, owner_id, caller_id) == (_BOT_ID, _USER_ID, _USER_ID)
    return BotFacts(
        bot_id=bot_id,
        bot_type="personal",
        active_engine="openclaw",
        owner_id=owner_id,
    )


async def _relay_call(_self, *, method: str, path: str, **_kwargs) -> EngineResult:
    if method == "GET":
        return EngineResult(
            data=[
                {
                    "id": _SESSION_ID,
                    "title": "Favorite session",
                    "agent_id": "main",
                    "message_count": 1,
                }
            ]
        )
    return EngineResult(data={})


def _seed_relay(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    bind_overrides(
        world,
        EngineRuntimeRelayProtocol,
        {"resolve_bot_off_loop": _resolve_bot, "call": _relay_call},
    )


def _boot_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


@endpoint_test(
    method="GET",
    path=f"{_BASE}/favorites",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_relay,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"items": [{"session_id": _SESSION_ID}]},
        },
    ),
)
def list_session_favorites_happy():
    """The acting user's favorite sessions are returned."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/favorites",
    scenario="missing_user_id",
    input=CaseInput(path_params={"bot_id": _BOT_ID}, headers=_HEADERS),
    seed=_boot_verifier,
    expect=ExpectError(status=422),
)
def list_session_favorites_error():
    """The required acting user cannot be omitted."""


@endpoint_test(
    method="PUT",
    path=f"{_BASE}/{{session_id}}/favorite",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "session_id": _SESSION_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_relay,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"session_id": _SESSION_ID, "favorited": True},
        },
    ),
)
def add_session_favorite_happy():
    """Favoriting a session returns its resulting state."""


@endpoint_test(
    method="PUT",
    path=f"{_BASE}/{{session_id}}/favorite",
    scenario="missing_user_id",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "session_id": _SESSION_ID}, headers=_HEADERS
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=422),
)
def add_session_favorite_error():
    """Favoriting requires an acting user."""


@endpoint_test(
    method="DELETE",
    path=f"{_BASE}/{{session_id}}/favorite",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "session_id": _SESSION_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_relay,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"session_id": _SESSION_ID, "favorited": False},
        },
    ),
)
def remove_session_favorite_happy():
    """Removing a favorite returns its resulting state."""


@endpoint_test(
    method="DELETE",
    path=f"{_BASE}/{{session_id}}/favorite",
    scenario="missing_user_id",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "session_id": _SESSION_ID}, headers=_HEADERS
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=422),
)
def remove_session_favorite_error():
    """Removing a favorite requires an acting user."""
