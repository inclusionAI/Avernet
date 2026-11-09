"""Endpoint-framework coverage for the public Bot catalog OpenAPI routes."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.bot_public.catalog_metadata import BotCatalogProfileNotFoundError
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

_USER_ID = "catalog-user"
_KEY = "catalog-endpoint-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal(*, app_only: bool = False) -> str:
    now = int(time.time())
    principals = []
    if not app_only:
        principals.append(
            {
                "type": "user",
                "subject": {
                    "id": _USER_ID,
                    "username": "catalog@example.test",
                },
            }
        )
    principals.append(
        {
            "type": "app",
            "tenant": "test",
            "app": {
                "app_id": 1,
                "app_name": "catalog-client",
                "owners": "test",
                "tenant": "test",
            },
        }
    )
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": principals,
        },
        _KEY,
        algorithm="HS256",
    )


def _boot_verifier() -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_search(world) -> None:
    _boot_verifier()

    def _search(_self, **_kwargs):
        return {"total": 0, "items": []}

    bind_overrides(
        world,
        BotPublicServiceProtocol,
        {"search_catalog_public_bots_by_keyword": _search},
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
    path="/openapi/v1/bots/catalog/search",
    scenario="catalog",
    seed=_seed_search,
    input=CaseInput(headers={PRINCIPAL_HEADER: _principal()}),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"total": 0, "items": []}},
    ),
)
def search_catalog():
    """The default platform reads the current TeamClaw catalogue."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/catalog/search",
    scenario="invalid_page",
    seed=_seed_search,
    input=CaseInput(
        query_params={"page": 0},
        headers={PRINCIPAL_HEADER: _principal()},
    ),
    expect=ExpectError(status=422, json_contains={"code": 422000}),
)
def search_invalid_page():
    """Search pagination remains validated."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/catalog/discover",
    scenario="app_only_catalog",
    seed=_seed_discover,
    input=CaseInput(
        query_params={"keyword": "automation"},
        headers={PRINCIPAL_HEADER: _principal(app_only=True)},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"total": 0, "items": []}},
    ),
)
def discover_app_only_catalog():
    """An authenticated application sees the same public catalogue."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/catalog/discover",
    scenario="missing_keyword",
    seed=_seed_discover,
    input=CaseInput(headers={PRINCIPAL_HEADER: _principal()}),
    expect=ExpectError(status=422, json_contains={"code": 422000}),
)
def discover_missing_keyword():
    """Discovery still requires a non-empty keyword."""


def _seed_profile(world) -> None:
    _boot_verifier()

    def _profile(_self, bot_uuid: str, **_kwargs):
        if bot_uuid == "missing-bot:owner-1":
            raise BotCatalogProfileNotFoundError(bot_uuid)
        return {
            "bot_id": "catalog-bot",
            "bot_uuid": bot_uuid,
            "bot_name": "Catalog Bot",
            "owner_name": "Owner",
            "description": "A public catalog entry",
            "engine_name": "openclaw",
            "capabilities": [
                {"capability_id": "antv", "display_name": "AntV 可视化图表"}
            ],
            "skills": [{"skill_id": "weekly-report", "display_name": "风太周报"}],
        }

    bind_overrides(
        world,
        BotPublicServiceProtocol,
        {"get_catalog_public_bot_profile": _profile},
    )


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/catalog/profile/{bot_uuid}",
    scenario="profile",
    seed=_seed_profile,
    input=CaseInput(
        path_params={"bot_uuid": "catalog-bot:owner-1"},
        headers={PRINCIPAL_HEADER: _principal()},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": "catalog-bot",
                "bot_uuid": "catalog-bot:owner-1",
                "bot_name": "Catalog Bot",
                "engine_name": "openclaw",
                "capabilities": [
                    {"capability_id": "antv", "display_name": "AntV 可视化图表"}
                ],
                "skills": [
                    {"skill_id": "weekly-report", "display_name": "风太周报"}
                ],
            },
        },
    ),
)
def profile_catalog():
    """The friend-request dialog reads the public profile from the catalogue."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/catalog/profile/{bot_uuid}",
    scenario="profile_not_found",
    seed=_seed_profile,
    input=CaseInput(
        path_params={"bot_uuid": "missing-bot:owner-1"},
        headers={PRINCIPAL_HEADER: _principal()},
    ),
    expect=ExpectError(status=404, json_contains={"code": 404000}),
)
def profile_not_found():
    """A bot outside the caller-visible catalogue keeps an enveloped 404."""
