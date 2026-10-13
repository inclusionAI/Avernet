"""Endpoint-runner coverage for the Local Skill security observation read."""

from __future__ import annotations

import time
import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.skill_query_service import SkillQueryServiceProtocol
from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    LocalSkillSecurityObservationRepositoryProtocol,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OWNER = "observation-owner"
_BOT_ID = "observation-bot"
_KEY = "observation-framework-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


class _Query:
    def __init__(self, *, missing: bool):
        self._missing = missing

    def get_local_skill(self, *, skill_id: str, actor_id: str):
        if self._missing:
            raise LocalSkillNotFoundError()
        return {
            "id": skill_id,
            "bolt_id": _BOT_ID,
            "user_id": _OWNER,
            "git_path": "local://observed",
        }


class _Repository:
    def get_formal_incarnation_id(self, **_kwargs):
        return None

    def get_latest_handoff_for_asset(self, **_kwargs):
        return None

    def get_identity_for_asset(self, **_kwargs):
        return None

    def get_current_for_asset(self, **_kwargs):
        return None

    def page_for_asset(self, **_kwargs):
        return 0, ()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {"type": "user", "subject": {"id": _OWNER, "username": "owner@test"}}
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _seed(world, *, missing: bool) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER)
    world.injector.binder.bind(
        SkillQueryServiceProtocol, to=_Query(missing=missing), scope=None
    )
    world.injector.binder.bind(
        LocalSkillSecurityObservationRepositoryProtocol, to=_Repository(), scope=None
    )


def _seed_happy(world) -> None:
    _seed(world, missing=False)


def _seed_missing(world) -> None:
    _seed(world, missing=True)


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/security-observations",
    scenario="unobserved_local_skill_is_empty",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_happy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "data": {
                "provider_skill_id": None,
                "current": None,
                "history": {"total": 0, "items": []},
            }
        },
    ),
)
def observations_happy():
    """An existing Local Skill can predate observation enrollment."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/security-observations",
    scenario="missing_local_skill_is_hidden",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_missing,
    expect=ExpectError(status=404),
)
def observations_error():
    """A missing asset does not reveal any security record."""
