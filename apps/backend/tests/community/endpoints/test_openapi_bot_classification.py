"""Registered endpoint cases for Gateway-facing Bot classification."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.repository.protocols.bot import BotRepository
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

_PATH = "/openapi/v1/bots/{bot_id}/classification"
_BOT_ID = "classification-endpoint-bot"
_OWNER_ID = "classification-bot-owner"
_TENANT = DEFAULT_AVERNET_TENANT
_KEY = "classification-endpoint-test-signing-key"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _headers() -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": "classification-viewer",
                        "username": "viewer@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )
    return {PRINCIPAL_HEADER: token}


def _seed_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    with avernet_tenant_scope(_TENANT):
        world.get(BotRepository).insert(
            {
                "bot_id": _BOT_ID,
                "bot_name": "Classification Bot",
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


def _assert_minimal_projection(response, _world) -> None:
    assert response.json()["data"] == {"bot_id": _BOT_ID, "bot_type": "service"}


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="authenticated_non_owner_classification",
    seed=_seed_bot,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"entity_id": _OWNER_ID},
        headers=_headers(),
    ),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
    extra_assertions=(_assert_minimal_projection,),
)
def classification_ok():
    """The assembled app resolves classification through the real Service API."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="bot_not_found",
    seed=_seed_bot,
    input=CaseInput(
        path_params={"bot_id": "classification-missing-bot"}, headers=_headers()
    ),
    expect=ExpectError(status=404, json_contains={"code": 404000, "data": None}),
)
def classification_not_found():
    """An authenticated missing-Bot lookup returns the public error envelope."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="authentication_required",
    seed=_seed_bot,
    input=CaseInput(path_params={"bot_id": _BOT_ID}),
    expect=ExpectError(status=401, json_contains={"code": 401000, "data": None}),
)
def classification_unauthenticated():
    """The assembled public authentication guard rejects anonymous callers."""
