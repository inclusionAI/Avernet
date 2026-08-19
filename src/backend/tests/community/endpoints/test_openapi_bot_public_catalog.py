"""Endpoint-framework coverage for the public Bot catalog OpenAPI routes.

The assembled application verifies a gateway-signed user principal. Search uses
the real service boundary with a deterministic repository-shaped result;
discovery replaces only the external BCSFuse boundary outcome. Both error cases
prove that a signed caller cannot select another ``user_id``.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_USER_ID = "public-catalog-user"
_KEY = "public-catalog-endpoint-signing-key-at-least-32-bytes"


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
                        "username": "public-catalog@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _boot_verifier() -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_auth_only(_world) -> None:
    _boot_verifier()


def _seed_search(world) -> None:
    _boot_verifier()

    def _search(_self, **_kwargs):
        return {"total": 0, "items": []}

    bind_overrides(
        world,
        BotPublicServiceProtocol,
        {"search_public_bots_by_keyword": _search},
    )


def _seed_discover(world) -> None:
    _boot_verifier()

    def _discover(_self, **_kwargs):
        return {
            "total": 0,
            "items": [],
            "context": {"recommend_response": {"recommendations": []}},
        }

    bind_overrides(
        world,
        BotDiscoverServiceProtocol,
        {"search_by_keyword": _discover},
    )


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/public/search",
    scenario="empty_catalog",
    seed=_seed_search,
    input=CaseInput(
        query_params={"user_id": _USER_ID},
        headers=_HEADERS,
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"total": 0, "items": []},
        },
    ),
)
def search_empty_catalog():
    """An authenticated caller receives the public catalog envelope."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/public/search",
    scenario="wrong_user",
    seed=_seed_auth_only,
    input=CaseInput(
        query_params={"user_id": "someone-else"},
        headers=_HEADERS,
    ),
    expect=ExpectError(
        status=403,
        json_contains={"code": 403001, "message": "Forbidden", "data": None},
    ),
)
def search_wrong_user():
    """A caller cannot search under another user's identity."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/public/discover",
    scenario="empty_recommendations",
    seed=_seed_discover,
    input=CaseInput(
        query_params={"user_id": _USER_ID, "keyword": "automation"},
        headers=_HEADERS,
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"total": 0, "items": []},
        },
    ),
)
def discover_empty_recommendations():
    """A valid empty recommendation result remains a successful page."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/public/discover",
    scenario="wrong_user",
    seed=_seed_auth_only,
    input=CaseInput(
        query_params={"user_id": "someone-else", "keyword": "automation"},
        headers=_HEADERS,
    ),
    expect=ExpectError(
        status=403,
        json_contains={"code": 403001, "message": "Forbidden", "data": None},
    ),
)
def discover_wrong_user():
    """A caller cannot request recommendations as another user."""
