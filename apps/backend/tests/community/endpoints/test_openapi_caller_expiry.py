"""Assembled OpenAPI happy/error contract for the administrative expiry command."""
from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.caller_expiry_correction_service import CallerExpiryCorrectionServiceProtocol
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, bind_overrides, endpoint_test

_KEY = "caller-expiry-framework-signing-key-at-least-32-bytes"
_PATH = "/openapi/v1/bots/{bot_id}/caller-instances/{caller_user_id}/token-expiry"
_BODY = {"entity_id": "entity", "binding_id": 7, "expires_at": "2000-01-01T00:00:00Z",
         "expected_expires_at": None, "reason": "verify refresh"}


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _name):
        return _Secret()


def _headers():
    now = int(time.time())
    payload = {"iss": "gateway", "aud": "backend", "iat": now, "exp": now + 3600,
               "principals": [{"type": "user", "subject": {"id": "admin", "username": "admin"}}]}
    return {PRINCIPAL_HEADER: jwt.encode(payload, _KEY, algorithm="HS256")}


def _seed(world):
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)

    def correct(_self, **kwargs):
        assert kwargs["actor_id"] == "admin"
        assert kwargs["caller_user_id"] == "caller"
        return {"instance_id": 1, "bot_id": "bot", "entity_id": "entity", "caller_user_id": "caller",
                "binding_id": 7, "previous_expires_at": None, "expires_at": "2000-01-01T00:00:00+00:00"}

    bind_overrides(world, CallerExpiryCorrectionServiceProtocol, {"correct": correct})


@endpoint_test(method="PATCH", path=_PATH, scenario="happy", seed=_seed,
    input=CaseInput(path_params={"bot_id": "bot", "caller_user_id": "caller"},
                    query_params={"user_id": "admin"}, headers=_headers(), json_body=_BODY),
    expect=ExpectSuccess(status=200, json_contains={"data": {"instance_id": 1, "binding_id": 7}}))
def correct_caller_expiry_happy():
    """The addressed caller and verified actor remain different identities."""


@endpoint_test(method="PATCH", path=_PATH, scenario="forbidden_user_scope", seed=_seed,
    input=CaseInput(path_params={"bot_id": "bot", "caller_user_id": "caller"},
                    query_params={"user_id": "someone-else"}, headers=_headers(), json_body=_BODY),
    expect=ExpectError(status=403))
def correct_caller_expiry_actor_spoof():
    """The target cannot replace the verified administrator actor."""
