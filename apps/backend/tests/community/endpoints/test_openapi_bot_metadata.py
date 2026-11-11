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
_OWNER_ID = "another-user"
_KEY = "metadata-endpoint-signing-key-at-least-32-bytes"
_QUERY = {"user_id": _USER_ID}


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
            "owner_id": _OWNER_ID,
            "owner_name": _OWNER_ID,
            "entity_id": _OWNER_ID,
            "entity_type": "staff",
            "creator_id": "another-user",
            "status": "ACTIVE",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
    )


def _seed_default_bots(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    repo = world.get(BotRepository)
    for owner_id in ("owner-a", "owner-b"):
        repo.insert(
            {
                "bot_id": "default",
                "bot_name": f"Default {owner_id}",
                "bot_desc": "Display-safe description",
                "owner_id": owner_id,
                "owner_name": owner_id,
                "entity_id": owner_id,
                "entity_type": "staff",
                "creator_id": owner_id,
                "status": "ACTIVE",
                "active_engine": "openclaw",
                "bot_type": "personal",
            }
        )


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/metadata/queries",
    scenario="resolves_known_ids",
    seed=_seed_bot,
    input=CaseInput(
        headers=_headers(),
        query_params=_QUERY,
        json_body={"bots": [{"bot_id": _BOT_ID, "owner_id": _OWNER_ID}]},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 1,
                "items": [
                    {
                        "bot_id": _BOT_ID,
                        "owner_id": _OWNER_ID,
                        "bot_name": "Metadata Bot",
                    }
                ],
            },
        },
    ),
)
def search_bot_metadata_ok():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/metadata/queries",
    scenario="resolves_default_bot_by_owner_pair",
    seed=_seed_default_bots,
    input=CaseInput(
        headers=_headers(),
        query_params=_QUERY,
        json_body={"bots": [{"bot_id": "default", "owner_id": "owner-b"}]},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 1,
                "items": [
                    {
                        "bot_id": "default",
                        "owner_id": "owner-b",
                        "bot_name": "Default owner-b",
                    }
                ],
            },
        },
    ),
)
def search_default_bot_metadata_by_owner_ok():
    """The owner half of the key prevents cross-user default Bot matches."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/metadata/queries",
    scenario="rejects_empty_batch",
    seed=lambda world: init_principal_verifier_config(
        _Resolver(), "test-key", strict=False
    ),
    input=CaseInput(headers=_headers(), query_params=_QUERY, json_body={"bots": []}),
    expect=ExpectError(status=422),
)
def search_bot_metadata_error():
    """The framework owns invocation."""
