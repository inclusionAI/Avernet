"""Registered endpoint cases for the transitional Bot access check."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.di.openapi_bot_access_check_config import (
    OpenApiBotAccessCheckConfig,
)
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    avernet_tenant_scope,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/openapi/v1/bots/{bot_id}/access"
_BOT_ID = "access-check-endpoint-bot"
_OWNER_ID = "access-check-bot-owner"
_APP_NAME = "access-check-partner"
_TENANT = DEFAULT_AVERNET_TENANT
_KEY = "access-check-endpoint-test-signing-key"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _token(principal: dict) -> dict[str, str]:
    now = int(time.time())
    claims = {
        "iss": "gateway",
        "aud": "backend",
        "iat": now,
        "exp": now + 3600,
        "principals": [principal],
    }
    return {PRINCIPAL_HEADER: jwt.encode(claims, _KEY, algorithm="HS256")}


def _app_headers() -> dict[str, str]:
    return _token(
        {
            "type": "app",
            "tenant": _TENANT,
            "app": {
                "app_id": 77,
                "app_name": _APP_NAME,
                "owners": "platform-team",
                "tenant": _TENANT,
                "app_type": "integration",
            },
        }
    )


def _human_headers() -> dict[str, str]:
    return _token(
        {
            "type": "user",
            "subject": {"id": _OWNER_ID, "username": "owner@example.test"},
        }
    )


def _seed(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    world.injector.binder.bind(
        OpenApiBotAccessCheckConfig,
        to=OpenApiBotAccessCheckConfig(app_names=frozenset({_APP_NAME})),
    )
    with avernet_tenant_scope(_TENANT):
        world.get(BotRepository).insert(
            {
                "bot_id": _BOT_ID,
                "bot_name": "Access Check Bot",
                "owner_id": _OWNER_ID,
                "owner_name": _OWNER_ID,
                "entity_id": _OWNER_ID,
                "entity_type": "staff",
                "creator_id": _OWNER_ID,
                "status": "ACTIVE",
                "active_engine": "openclaw",
                "bot_type": "service",
            }
        )


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="owner_has_owner_access",
    seed=_seed,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"entity_id": _OWNER_ID, "user_id": _OWNER_ID},
        headers=_app_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "data": {
                "bot_id": _BOT_ID,
                "entity_id": _OWNER_ID,
                "user_id": _OWNER_ID,
                "has_access": True,
                "level": "owner",
                "required_level": "member",
            }
        },
    ),
)
def access_check_owner_ok():
    """Body intentionally empty — the framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="unknown_bot_has_no_access",
    seed=_seed,
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"},
        query_params={"entity_id": _OWNER_ID, "user_id": _OWNER_ID},
        headers=_app_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"has_access": False, "level": "none"}},
    ),
)
def access_check_unknown_bot():
    pass


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="human_caller_forbidden",
    seed=_seed,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"entity_id": _OWNER_ID, "user_id": _OWNER_ID},
        headers=_human_headers(),
    ),
    expect=ExpectError(status=403, json_contains={"message": "Forbidden"}),
)
def access_check_human_forbidden():
    pass
