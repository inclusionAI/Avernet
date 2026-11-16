"""Assembled endpoint coverage for the owner-scoped one-shot collaboration repair."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import jwt
from injector import InstanceProvider

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.di.modules.collaboration_repair_module import (
    CollaborationRepairFactory,
)
from agentclaw.community.kernel.collaboration_repair import (
    RepairItem,
    RepairReport,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "collab-repair-owner"
_BOT_ID = "collab-repair-bot"
_BOT_PK = 4102
_KEY = "collab-repair-signing-key-at-least-32-bytes"
_REPAIR_PATH = "/openapi/v1/bots/{bot_id}/collaboration/repair"
_PATH_PARAMS = {"bot_id": _BOT_ID}


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal(user_id: str = _OWNER) -> str:
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
                        "id": user_id,
                        "username": f"{user_id}@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _query(user_id: str = _OWNER) -> dict[str, str]:
    return {"user_id": user_id}


class _RecordingRepairService:
    def __init__(self, report: RepairReport) -> None:
        self._report = report

    async def repair(self, _command):
        return self._report


class _RecordingRepairFactory:
    def __init__(self, service: _RecordingRepairService) -> None:
        self._service = service
        self.opened_headers: list[dict[str, str]] = []

    @asynccontextmanager
    async def open(self, profile_headers):
        self.opened_headers.append(dict(profile_headers))
        yield self._service


succeded_report = RepairReport(
    bot_id=_BOT_ID,
    status="succeeded",
    items=(
        RepairItem("ownership", "succeeded"),
        RepairItem("profile", "succeeded"),
        RepairItem("managers", "succeeded"),
    ),
)


def _seed(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    make_staff_user(world, user_id=_OWNER)

    def get_bot(_self, _bot_id, _owner_id):
        return {"bot_id": _BOT_ID, "owner_id": _OWNER, "env": "dev", "id": _BOT_PK}

    bind_overrides(world, BotServiceProtocol, {"get_bot": get_bot})
    world.injector.binder.bind(
        CollaborationRepairFactory,
        to=InstanceProvider(_RecordingRepairFactory(_RecordingRepairService(succeded_report))),
        scope=None,
    )


endpoint_test(
    method="POST",
    path=_REPAIR_PATH,
    scenario="happy",
    input=CaseInput(
        path_params=_PATH_PARAMS,
        query_params=_query(),
        headers={PRINCIPAL_HEADER: _principal()},
    ),
    seed=_seed,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "data": {
                "bot_id": _BOT_ID,
                "status": "succeeded",
                "items": [
                    {"component": "ownership", "status": "succeeded", "error_code": None},
                    {"component": "profile", "status": "succeeded", "error_code": None},
                    {"component": "managers", "status": "succeeded", "error_code": None},
                ],
            }
        },
    ),
)(lambda: None)

endpoint_test(
    method="POST",
    path=_REPAIR_PATH,
    scenario="forbidden_user_scope",
    input=CaseInput(
        path_params=_PATH_PARAMS,
        query_params=_query("another-user"),
        headers={PRINCIPAL_HEADER: _principal()},
    ),
    seed=_seed,
    expect=ExpectError(
        status=403,
        json_contains={"code": 403000, "data": None},
    ),
)(lambda: None)

endpoint_test(
    method="POST",
    path=_REPAIR_PATH,
    scenario="write_payload_refused",
    input=CaseInput(
        path_params=_PATH_PARAMS,
        query_params=_query(),
        headers={PRINCIPAL_HEADER: _principal()},
        json_body={"tenant": "other-tenant"},
    ),
    seed=_seed,
    expect=ExpectError(
        status=400,
        json_contains={"data": None},
    ),
)(lambda: None)
