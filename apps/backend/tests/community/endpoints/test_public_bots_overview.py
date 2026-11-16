"""Endpoint coverage for GET /openapi/v1/bots/{bot_id}/overview."""
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
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

BOT_ID = "public_overview_bot"
OWNER = "owner_overview"
PATH = "/openapi/v1/bots/{bot_id}/overview"
_KEY = "overview-endpoint-test-signing-key"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _app_only_headers() -> dict[str, str]:
    """A gateway-signed principal naming only an application — no end user."""
    now = int(time.time())
    claims = {
        "iss": "gateway",
        "aud": "backend",
        "iat": now,
        "exp": now + 3600,
        "principals": [
            {
                "type": "app",
                "tenant": DEFAULT_AVERNET_TENANT,
                "app": {
                    "app_id": 78,
                    "app_name": "overview-reader",
                    "owners": "platform-team",
                    "tenant": DEFAULT_AVERNET_TENANT,
                    "app_type": "integration",
                },
            }
        ],
    }
    return {PRINCIPAL_HEADER: jwt.encode(claims, _KEY, algorithm="HS256")}


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_bot(world) -> None:
    _seed_verifier(world)
    with avernet_tenant_scope(DEFAULT_AVERNET_TENANT):
        world.get(BotRepository).insert(
            {
                "bot_id": BOT_ID,
                "bot_name": "Public Overview Bot",
                "owner_id": OWNER,
                "owner_name": OWNER,
                "creator_id": OWNER,
                "entity_id": OWNER,
                "entity_type": "staff",
                "active_engine": "claude_code",
                "bot_type": "personal",
                "status": "ACTIVE",
                "device_id": "device_overview",
            }
        )


def _assert_no_user_data(response, _world) -> None:
    body = response.text
    assert OWNER not in body
    assert "device_overview" not in body


@endpoint_test(
    method="GET",
    path=PATH,
    scenario="happy",
    input=CaseInput(path_params={"bot_id": BOT_ID}, headers=_app_only_headers()),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "data": {
                "bot_id": BOT_ID,
                "engine": "claude_code",
                "status": "ACTIVE",
                "skills": [],
                "mcps": [],
                "payload": {"engine": "claude_code", "bot_type": "personal"},
            }
        },
    ),
    extra_assertions=(_assert_no_user_data,),
)
def get_bot_overview_happy():
    """App-only caller; AICoding-family Bot gets its payload; no owner leaks."""


@endpoint_test(
    method="GET",
    path=PATH,
    scenario="not_found",
    input=CaseInput(
        path_params={"bot_id": "missing_overview_bot"}, headers=_app_only_headers()
    ),
    seed=_seed_verifier,
    expect=ExpectError(status=404),
)
def get_bot_overview_not_found():
    """Unknown Bot is a 404."""


@endpoint_test(
    method="GET",
    path=PATH,
    scenario="unsigned_caller_refused",
    input=CaseInput(path_params={"bot_id": BOT_ID}),
    seed=_seed_bot,
    expect=ExpectError(status=401),
)
def get_bot_overview_unsigned():
    """No gateway-signed principal, no answer."""
