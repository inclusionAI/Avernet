"""Assembled-app coverage through real DI, authorization and persistence."""

import time
from types import SimpleNamespace
import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OWNER = "version-owner"
_KEY = "version-framework-test-signing-key-at-least-32-bytes"


class SecretResolver:
    def get_secret(self, _):
        return SimpleNamespace(secret_user="test", secret_value=_KEY)


def seed(world):
    init_principal_verifier_config(SecretResolver(), "test-key", strict=False)
    world.get(BotRepository).insert(
        {
            "bot_id": "version-bot",
            "bot_name": "Version Bot",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "status": "ACTIVE",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
    )


def request(bot_id):
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
                    "subject": {"id": _OWNER, "username": _OWNER},
                    "resolved_by": {"method": "buc"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )
    return CaseInput(
        path_params={"bot_id": bot_id},
        query_params={"user_id": _OWNER},
        headers={PRINCIPAL_HEADER: token},
    )


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/engine-version",
    scenario="happy",
    seed=seed,
    input=request("version-bot"),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "detection": {"status": "read_failed", "reason": "device_not_bound"},
                "restart_hint": "unknown",
            },
        },
    ),
)
def version_unbound():
    pass


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/engine-version",
    scenario="error",
    seed=seed,
    input=request("missing-bot"),
    expect=ExpectError(status=404, json_contains={"code": 404000, "data": None}),
)
def version_missing():
    pass
