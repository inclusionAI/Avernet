"""Endpoint-framework coverage for the Bot Workshop operational surfaces.

These cases exercise the assembled public application with a gateway-signed
human principal. Device inventory and the accepted health-check command cross
real external/runtime boundaries, so their deterministic replies are supplied
through per-test injector seams rather than by patching production classes.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.device_service import DeviceServiceProtocol
from agentclaw.community.api.health_diagnosis_service import (
    HealthDiagnosisServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "bot-workshop-owner"
_SERVICE_BOT = "bot-workshop-service"
_PERSONAL_BOT = "bot-workshop-personal"
_INSTANCE = "container-abnormal-1"
_KEY = "bot-workshop-endpoint-signing-key-at-least-32-bytes"


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
                        "id": _OWNER,
                        "username": "bot-workshop@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}


def _boot_verifier() -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_no_bot(_world) -> None:
    _boot_verifier()


def _seed_service_bot(world) -> None:
    _boot_verifier()
    make_bot(
        world,
        bot_id=_SERVICE_BOT,
        owner_id=_OWNER,
        bot_type="service",
        status="ACTIVE",
    )

    def _instances(_self, *args, **kwargs):
        return {
            "devices": [
                {
                    "device_uuid": _INSTANCE,
                    "health_status": "ABNORMAL",
                    "status": "ACTIVE",
                    "engine_type": "openclaw",
                    "provider_type": "arca",
                    "provider_device_id": "provider-instance-1",
                    "gmt_create": "2026-08-19T08:00:00+00:00",
                }
            ]
        }

    def _restart(_self, *args, **kwargs):
        return {"publish_id": 101}

    bind_overrides(
        world,
        DeviceServiceProtocol,
        {
            "get_instances_by_bot": _instances,
            "restart_device_by_bot": _restart,
        },
    )


def _seed_personal_bot(world) -> None:
    _boot_verifier()
    world.get(BotRepository).insert(
        {
            "bot_id": _PERSONAL_BOT,
            "bot_name": "Bot Workshop Personal",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "bot_type": "personal",
            "status": "ACTIVE",
            "active_engine": "openclaw",
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
        }
    )


def _seed_health_start(world) -> None:
    _seed_personal_bot(world)

    async def _start(_self, *, bot_id: str, owner_id: str, operator_id: str):
        return {"scan_id": 42, "bot_id": bot_id, "status": "scanning"}

    bind_overrides(world, HealthDiagnosisServiceProtocol, {"start": _start})


# Containers


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/containers",
    scenario="lists_live_service_instances",
    input=CaseInput(
        path_params={"bot_id": _SERVICE_BOT},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _SERVICE_BOT,
                "summary": {
                    "total": 1,
                    "healthy": 0,
                    "abnormal": 1,
                    "restarting": 0,
                    "unknown": 0,
                },
                "instances": [{"id": _INSTANCE, "status": "abnormal"}],
            },
        },
    ),
)
def list_containers_ok():
    """The public projection keeps provider internals out of the summary."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/containers",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "missing-bot"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def list_containers_unknown_bot():
    """An absent addressed Bot is masked as not found."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/containers/{instance_id}/restart",
    scenario="restarts_an_abnormal_instance",
    input=CaseInput(
        path_params={"bot_id": _SERVICE_BOT, "instance_id": _INSTANCE},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=202,
        json_contains={
            "code": 202000,
            "data": {
                "bot_id": _SERVICE_BOT,
                "instance_id": _INSTANCE,
                "publish_id": 101,
                "accepted": True,
            },
        },
    ),
)
def restart_container_ok():
    """Only an instance found in this Bot's abnormal inventory is restarted."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/containers/{instance_id}/restart",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "missing-bot", "instance_id": _INSTANCE},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def restart_container_unknown_bot():
    """The owner check runs before an instance id can be acted on."""


# Health diagnostics


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/diagnostics/health",
    scenario="reports_not_run_before_the_first_scan",
    input=CaseInput(
        path_params={"bot_id": _PERSONAL_BOT},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_personal_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "found": False,
                "bot_id": _PERSONAL_BOT,
                "status": "not_run",
            },
        },
    ),
)
def get_health_not_run():
    """No persisted scan is a successful empty state, not a 404."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/diagnostics/health",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "missing-bot"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def get_health_unknown_bot():
    """Diagnosis state is never disclosed for an unresolvable Bot."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/diagnostics/health-check",
    scenario="accepts_a_new_scan",
    input=CaseInput(
        path_params={"bot_id": _PERSONAL_BOT},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_health_start,
    expect=ExpectSuccess(
        status=202,
        json_contains={
            "code": 202000,
            "data": {
                "scan_id": 42,
                "bot_id": _PERSONAL_BOT,
                "status": "scanning",
            },
        },
    ),
)
def start_health_check_ok():
    """The route returns the persisted scan identity as an accepted command."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/diagnostics/health-check",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "missing-bot"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def start_health_check_unknown_bot():
    """Authorization precedes creation of a diagnosis record."""


# Data initialization status


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/data-init",
    scenario="reports_not_started_for_a_new_personal_bot",
    input=CaseInput(
        path_params={"bot_id": _PERSONAL_BOT},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_personal_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _PERSONAL_BOT,
                "status": "not_started",
                "started_at": None,
            },
        },
    ),
)
def get_data_init_not_started():
    """The safe status projection does not expose the Bot ext bag."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/data-init",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "missing-bot"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def get_data_init_unknown_bot():
    """The owner-scoped Bot lookup masks absence before reading status."""
