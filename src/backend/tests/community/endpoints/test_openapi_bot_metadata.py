"""Endpoint coverage for display-safe batch Bot metadata resolution."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_BOT_ID = "metadata-endpoint-bot"
_USER_ID = "metadata-endpoint-user"
_KEY = "metadata-endpoint-signing-key-at-least-32-bytes"


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
                    "tenant": "metadata-endpoint-test",
                    "subject": {"id": _USER_ID, "username": "metadata@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )
    return {PRINCIPAL_HEADER: token}


def _seed_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    world.get(BotRepository).insert(
        {
            "bot_id": _BOT_ID,
            "bot_name": "Metadata Bot",
            "bot_desc": "Display-safe description",
            "owner_id": "another-user",
            "owner_name": "another-user",
            "entity_id": "another-user",
            "entity_type": "staff",
            "creator_id": "another-user",
            "status": "ACTIVE",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
    )


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/metadata/search",
    scenario="resolves_known_ids",
    seed=_seed_bot,
    input=CaseInput(headers=_headers(), json_body={"bot_ids": [_BOT_ID]}),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 1,
                "items": [{"bot_id": _BOT_ID, "bot_name": "Metadata Bot"}],
            },
        },
    ),
)
def search_bot_metadata_ok():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/metadata/search",
    scenario="rejects_empty_batch",
    seed=lambda world: init_principal_verifier_config(
        _Resolver(), "test-key", strict=False
    ),
    input=CaseInput(headers=_headers(), json_body={"bot_ids": []}),
    expect=ExpectError(status=422),
)
def search_bot_metadata_error():
    """The framework owns invocation."""
