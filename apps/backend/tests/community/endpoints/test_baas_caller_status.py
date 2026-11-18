"""Real DI/SQLite cases for the authenticated BaaS read-only Caller endpoint."""
from __future__ import annotations

import time

import jwt

from agentclaw.community.core.repository.protocols.chat import ExpertChatInstanceRepository
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_PATH = "/api/v1/expert-chats/app-caller-instance/status"
_PARAMS = {"bot_id": "baas-status-bot", "owner_id": "baas-status-owner", "user_id": "baas-status-other-caller"}
_KEY = "baas-status-framework-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _name: str) -> _Secret:
        return _Secret()


def _headers() -> dict[str, str]:
    now = int(time.time())
    return {"X-Avernet-Principal": jwt.encode(
        {"iss": "baas", "iat": now, "exp": now + 3600, "tenant": "untrusted-tenant"},
        _KEY, algorithm="HS256",
    )}


def _seed(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    make_bot(world, bot_id=_PARAMS["bot_id"], owner_id=_PARAMS["owner_id"], active_engine="openclaw")


def _seed_pending(world) -> None:
    _seed(world)
    world.get(ExpertChatInstanceRepository).upsert_instance(**_PARAMS, status="init", ext={"version": 1})


def _assert_absent(response, world) -> None:
    assert response.json()["data"] == {"exists": False, "alive": False}
    assert world.get(ExpertChatInstanceRepository).get_instance(
        _PARAMS["user_id"], _PARAMS["bot_id"], _PARAMS["owner_id"],
    ) is None


def _assert_pending(response, world) -> None:
    assert response.json()["data"] == {"exists": False, "alive": False}
    row = world.get(ExpertChatInstanceRepository).get_instance(
        _PARAMS["user_id"], _PARAMS["bot_id"], _PARAMS["owner_id"],
    )
    assert row["status"] == "init" and row["ext"] == {"version": 1}


@endpoint_test(
    method="GET", path=_PATH, scenario="baas_private_bot_other_caller_absent",
    seed=_seed, input=CaseInput(query_params=_PARAMS, headers=_headers()),
    expect=ExpectSuccess(json_contains={"success": True, "data": {"exists": False, "alive": False}}),
    extra_assertions=(_assert_absent,),
)
def baas_status_absent():
    """Service credentials authorize this private read without an app grant."""


@endpoint_test(
    method="GET", path=_PATH, scenario="baas_pending_caller_is_not_provisioned",
    seed=_seed_pending, input=CaseInput(query_params=_PARAMS, headers=_headers()),
    expect=ExpectSuccess(json_contains={"success": True, "data": {"exists": False, "alive": False}}),
    extra_assertions=(_assert_pending,),
)
def baas_status_pending():
    """The real instance service preserves an unallocated existing ledger."""


@endpoint_test(
    method="GET", path=_PATH, scenario="unverified_service_is_refused",
    seed=_seed, input=CaseInput(query_params=_PARAMS),
    expect=ExpectError(status=401),
)
def baas_status_auth_required():
    """No signed service credential means no private Caller observation."""


@endpoint_test(
    method="GET", path=_PATH, scenario="missing_source_bot_is_not_successful_absence",
    seed=_seed, input=CaseInput(query_params={**_PARAMS, "bot_id": "missing-baas-status-bot"}, headers=_headers()),
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 403, "data": None}),
)
def baas_status_missing_bot():
    """A missing source Bot is not converted to a successful false/false state."""
