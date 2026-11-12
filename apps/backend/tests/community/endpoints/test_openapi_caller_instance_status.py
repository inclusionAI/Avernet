"""Registered caller-status cases using the real services and SQLite ledger."""
from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.repository.protocols.chat import ExpertChatInstanceRepository
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_PATH = "/openapi/v1/bots/{bot_id}/caller-instance/status"
_BOT = "caller-status-framework-bot"
_OWNER = "caller-status-framework-owner"
_STRANGER = "caller-status-framework-stranger"
_KEY = "caller-status-framework-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _headers(user_id: str) -> dict[str, str]:
    now = int(time.time())
    return {PRINCIPAL_HEADER: jwt.encode(
        {
            "iss": "gateway", "aud": "backend", "iat": now, "exp": now + 3600,
            "principals": [{
                "type": "user", "subject": {"id": user_id, "username": "caller@example.test"},
            }],
        },
        _KEY, algorithm="HS256",
    )}


def _seed(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    make_bot(world, bot_id=_BOT, owner_id=_OWNER, active_engine="openclaw")


def _seed_pending(world) -> None:
    _seed(world)
    world.get(ExpertChatInstanceRepository).upsert_instance(
        user_id=_OWNER, bot_id=_BOT, owner_id=_OWNER, status="init", ext={"version": 1},
    )


def _assert_no_allocation(response, world) -> None:
    assert response.json()["data"] == {"exists": False, "alive": False}
    assert world.get(ExpertChatInstanceRepository).get_instance(_OWNER, _BOT, _OWNER) is None


def _assert_pending_unchanged(response, world) -> None:
    assert response.json()["data"] == {"exists": False, "alive": False}
    row = world.get(ExpertChatInstanceRepository).get_instance(_OWNER, _BOT, _OWNER)
    assert row["status"] == "init"
    assert row["ext"] == {"version": 1}


@endpoint_test(
    method="GET", path=_PATH, scenario="owner_without_caller_is_not_allocated",
    seed=_seed,
    input=CaseInput(
        path_params={"bot_id": _BOT},
        query_params={"user_id": _OWNER, "entity_id": _OWNER}, headers=_headers(_OWNER),
    ),
    expect=ExpectSuccess(status=200, json_contains={"data": {"exists": False, "alive": False}}),
    extra_assertions=(_assert_no_allocation,),
)
def caller_status_absent():
    """The real service read must not allocate a ledger row."""


@endpoint_test(
    method="GET", path=_PATH, scenario="pending_caller_without_uuid_stays_unchanged",
    seed=_seed_pending,
    input=CaseInput(
        path_params={"bot_id": _BOT},
        query_params={"user_id": _OWNER, "entity_id": _OWNER}, headers=_headers(_OWNER),
    ),
    expect=ExpectSuccess(status=200, json_contains={"data": {"exists": False, "alive": False}}),
    extra_assertions=(_assert_pending_unchanged,),
)
def caller_status_pending():
    """An unallocated existing row must not be provisioned or rewritten."""


@endpoint_test(
    method="GET", path=_PATH, scenario="private_bot_is_hidden_from_nonowner",
    seed=_seed,
    input=CaseInput(
        path_params={"bot_id": _BOT},
        query_params={"user_id": _STRANGER, "entity_id": _OWNER}, headers=_headers(_STRANGER),
    ),
    expect=ExpectError(status=404, json_contains={"message": "Not found"}),
)
def caller_status_private_forbidden():
    """Possessing a valid user principal does not grant private Bot access."""


@endpoint_test(
    method="GET", path=_PATH, scenario="unknown_bot_is_not_found",
    seed=_seed,
    input=CaseInput(
        path_params={"bot_id": "caller-status-framework-missing"},
        query_params={"user_id": _OWNER, "entity_id": _OWNER}, headers=_headers(_OWNER),
    ),
    expect=ExpectError(status=404, json_contains={"message": "Not found"}),
)
def caller_status_missing_bot():
    """A missing Bot remains distinct from an authorized absent Caller."""
