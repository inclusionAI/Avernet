"""Endpoint-runner coverage for Bot Skill content and parameter contracts."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_skill_asset_service import BotSkillAssetServiceProtocol
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_OWNER = "asset-owner"
_BOT_ID = "asset-bot"
_KEY = "asset-framework-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


class _Asset:
    def __init__(self, *, missing: bool = False) -> None:
        self._missing = missing
        self.parameters = {"enabled": False}

    def get_skill(self, **_kwargs):
        if self._missing:
            raise LocalSkillNotFoundError()
        return {
            "id": "1",
            "name": "asset",
            "description": "asset test",
            "git_path": "local://asset",
            "user_id": _OWNER,
            "bolt_id": _BOT_ID,
            "active": False,
        }

    async def get_content(self, **_kwargs) -> str:
        self.get_skill()
        return "---\nname: asset\n---\n# Asset"

    async def get_parameters(self, **_kwargs):
        self.get_skill()
        return self.parameters

    async def replace_parameters(self, *, parameters, **_kwargs):
        self.get_skill()
        self.parameters = parameters
        return parameters


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {"type": "user", "subject": {"id": _OWNER, "username": "asset@test"}}
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _seed_asset(world, *, missing: bool = False) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    # The seven ``{skill_id}`` operations declare ``Check(MEMBER)``, so
    # ``bot_access`` resolves ``(bot_id, owner_id)`` against the real
    # ``BotRepository`` before the handler runs. The asset service below is a
    # double and can answer without a Bot row; the gate cannot, and refuses.
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER)
    world.injector.binder.bind(
        BotSkillAssetServiceProtocol, to=_Asset(missing=missing), scope=None
    )


def _seed_happy(world) -> None:
    _seed_asset(world)


def _seed_missing(world) -> None:
    _seed_asset(world, missing=True)


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/content",
    scenario="returns_consumable_skill_md",
    input=CaseInput(path_params={"bot_id": _BOT_ID, "skill_id": "1"}, query_params={"user_id": _OWNER}, headers=_HEADERS),
    seed=_seed_happy,
    expect=ExpectSuccess(status=200, json_contains={"data": {"content": "---\nname: asset\n---\n# Asset"}}),
)
def content_happy():
    """Content returns raw consumable SKILL.md."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/content",
    scenario="masks_missing_asset",
    input=CaseInput(path_params={"bot_id": _BOT_ID, "skill_id": "1"}, query_params={"user_id": _OWNER}, headers=_HEADERS),
    seed=_seed_missing,
    expect=ExpectError(status=404),
)
def content_error():
    """Content shares item-not-found behavior."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters",
    scenario="returns_bot_parameters",
    input=CaseInput(path_params={"bot_id": _BOT_ID, "skill_id": "1"}, query_params={"user_id": _OWNER}, headers=_HEADERS),
    seed=_seed_happy,
    expect=ExpectSuccess(status=200, json_contains={"data": {"parameters": {"enabled": False}}}),
)
def parameters_get_happy():
    """Get returns the complete Bot parameter object."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters",
    scenario="masks_missing_asset",
    input=CaseInput(path_params={"bot_id": _BOT_ID, "skill_id": "1"}, query_params={"user_id": _OWNER}, headers=_HEADERS),
    seed=_seed_missing,
    expect=ExpectError(status=404),
)
def parameters_get_error():
    """Get parameters shares item-not-found behavior."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters",
    scenario="replaces_complete_bot_parameters",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
        json_body={"parameters": {"enabled": False}},
    ),
    seed=_seed_happy,
    expect=ExpectSuccess(status=200, json_contains={"data": {"parameters": {"enabled": False}}}),
)
def parameters_put_happy():
    """Put persists a full replacement, including false values."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters",
    scenario="masks_missing_asset",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
        json_body={"parameters": {}},
    ),
    seed=_seed_missing,
    expect=ExpectError(status=404),
)
def parameters_put_error():
    """Put parameters shares item-not-found behavior."""
