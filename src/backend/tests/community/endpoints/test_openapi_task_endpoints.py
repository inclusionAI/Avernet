"""Endpoint-framework coverage for the public collaboration task routes.

The coverage gate tracks the externally served ``/openapi/v1`` paths separately
from their internal ``/api/v1`` counterparts. Each operation has a verified
principal happy path and an unauthenticated rejection path.
"""
from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    TaskInfo,
    TaskSpec,
)
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, bind_overrides, endpoint_test
from agentclaw.community.api.task.task_grant_service import (
    GRANTED,
    REVOKED,
    GrantResult,
    RevokeResult,
    TaskClaimGrantServiceProtocol,
)

_BASE = "/openapi/v1/collaboration/tasks"
_CALLER = "public-task-owner"
_KEY = "public-task-framework-signing-key-32b"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _boot_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [{
                "type": "user",
                "subject": {"id": _CALLER, "username": "public-task@example.test"},
            }],
        },
        _KEY,
        algorithm="HS256",
    )


def _headers() -> dict[str, str]:
    return {PRINCIPAL_HEADER: _principal()}


def _task_body() -> dict:
    return {
        "task_spec": {
            "metadata": {"title": "Public task", "instruction": "exercise public route"},
            "context": {"background": "endpoint coverage", "extend_props": {}},
            "goal": {"objective": "cover task endpoint", "acceptances": []},
        },
        "source_type": "bot",
        "owner_user_id": _CALLER,
        "owner_bot_id": "public-task-bot",
        "execution_config": {"task_type": "dynamic"},
    }


def _seed_graph(world, task_id: str) -> None:
    world.get(TaskGraphService).initialize_graph(
        TaskInfo(
            task_spec=TaskSpec(
                metadata=Metadata(task_id=task_id, title="Public task", instruction="test"),
                context=Context(background="endpoint coverage"),
                goal=Goal(objective="cover task endpoint", acceptances=[
                    AcceptanceCriteria(id="ac1", description="covered"),
                ]),
            ),
            source_type="bot",
            owner_bot_id="public-task-bot",
            execution_config={"task_type": "dynamic"},
        )
    )


@endpoint_test(
    method="POST",
    path=f"{_BASE}/execute",
    scenario="authenticated_submission",
    input=CaseInput(headers=_headers(), json_body=_task_body()),
    seed=_boot_verifier,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"success": True}}),
)
def execute_authenticated():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/execute",
    scenario="unauthenticated",
    input=CaseInput(json_body=_task_body()),
    seed=_boot_verifier,
    expect=ExpectError(status=401, json_contains={"code": 401000, "message": "Unauthorized"}),
)
def execute_unauthenticated():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/dashboard",
    scenario="authenticated_existing_task",
    input=CaseInput(headers=_headers(), query_params={"task_id": "public-task-dashboard"}),
    seed=lambda world: (_boot_verifier(world), _seed_graph(world, "public-task-dashboard")),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"tasks": [{"task_id": "public-task-dashboard"}]}},
    ),
)
def dashboard_authenticated():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/dashboard",
    scenario="unauthenticated",
    input=CaseInput(query_params={"task_id": "public-task-dashboard"}),
    seed=_boot_verifier,
    expect=ExpectError(status=401, json_contains={"code": 401000, "message": "Unauthorized"}),
)
def dashboard_unauthenticated():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/list",
    scenario="authenticated_owner_list",
    input=CaseInput(headers=_headers(), query_params={"user_id": _CALLER}),
    seed=_boot_verifier,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def list_authenticated():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/list",
    scenario="unauthenticated",
    input=CaseInput(),
    seed=_boot_verifier,
    expect=ExpectError(status=401, json_contains={"code": 401000, "message": "Unauthorized"}),
)
def list_unauthenticated():
    pass


# ── 任务认领 grant/revoke(public openapi face)─────────────────────────────────
_GRANT_BODY = {"bcs_bot_id": "public-task-bot:146836"}


def _seed_grant_service(world) -> None:
    async def grant(_self, *, bcs_bot_id, cookie, referer, operator):
        return GrantResult(bcs_bot_id=bcs_bot_id, api_key_prefix="pub", grant_status=GRANTED, operator=operator)

    async def revoke(_self, *, bcs_bot_id, cookie, referer, operator):
        return RevokeResult(bcs_bot_id=bcs_bot_id, grant_status=REVOKED)

    bind_overrides(world, TaskClaimGrantServiceProtocol, {"grant": grant, "revoke": revoke})


@endpoint_test(
    method="POST",
    path=f"{_BASE}/grant",
    scenario="happy_ok",
    seed=lambda w: (_boot_verifier(w), _seed_grant_service(w)),
    input=CaseInput(headers=_headers(), json_body=_GRANT_BODY),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"grant_status": "granted"}}),
)
def grant_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/grant",
    scenario="unauthenticated",
    input=CaseInput(json_body=_GRANT_BODY),
    seed=_boot_verifier,
    expect=ExpectError(status=401, json_contains={"code": 401000, "message": "Unauthorized"}),
)
def grant_unauthenticated():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/revoke",
    scenario="happy_ok",
    seed=lambda w: (_boot_verifier(w), _seed_grant_service(w)),
    input=CaseInput(headers=_headers(), json_body=_GRANT_BODY),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"grant_status": "revoked"}}),
)
def revoke_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/revoke",
    scenario="unauthenticated",
    input=CaseInput(json_body=_GRANT_BODY),
    seed=_boot_verifier,
    expect=ExpectError(status=401, json_contains={"code": 401000, "message": "Unauthorized"}),
)
def revoke_unauthenticated():
    pass
