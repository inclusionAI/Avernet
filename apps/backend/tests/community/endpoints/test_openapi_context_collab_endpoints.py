"""Endpoint-framework coverage for the public context-collab routes (spec §6.1).

The context-collab surface is a twin-face adapter (openapi_v1/task/router.py's
rule): this file exercises the ``/openapi/v1/collaboration/task/context_collab``
copy the gateway's spanner front door serves, while the internal ``/api/v1`` twin
is covered in ``test_context_collab_endpoints.py`` with the real service over a
real repository. Cases here pin what the public face adds on top: the signed
principal → ActorContext binding (``PrincipalRef("human", principal.user_id)``,
``source="openapi"``), the actor_ref reconciliation against that identity, and
the unified envelope/error mapping through the public route class.

Every case follows ``test_openapi_task_endpoints.py``: ``_boot_verifier`` boots
the principal verifier with a fixed signing key, ``_principal()`` mints the
gateway-signed ``X-Avernet-Principal`` header, and missing/bad headers answer
the surface's uniform 401 envelope. The happy/error cases below also satisfy the
framework's frozen-baseline coverage gate for the three new routes.
"""
from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, bind_overrides, endpoint_test
from agentclaw.community.api.task.context_collab_service import (
    ContextCollabServiceProtocol,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ContextCollabCreatedResult,
    ContextCollabStatusProjection,
    EventAcceptedResult,
    TaskProgressProjectionDTO,
)

_BASE = "/openapi/v1/collaboration/task/context_collab"  # spec §6.1: singular "task"
_CALLER = "u_1001"  # matches the §6.2/§6.3 example bodies verbatim
_KEY = "context-collab-framework-signing-key-32b"


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
                "subject": {"id": _CALLER, "username": "context-collab@example.test"},
            }],
        },
        _KEY,
        algorithm="HS256",
    )


def _headers() -> dict[str, str]:
    return {PRINCIPAL_HEADER: _principal()}


_BAD_HEADERS = {PRINCIPAL_HEADER: "not-a-gateway-signed-token"}


def _create_body() -> dict:
    """spec §6.2 指定执行请求示例 (verbatim JSON)."""
    return {
        "request_id": "create-collet-001",
        "space": {"space_id": "space_workspace_001", "environment": "prod"},
        "principal_ref": {"principal_type": "human", "principal_id": "u_1001"},
        "product_kind": "team_todo",
        "workflow_kind": "team_todo_execution",
        "method_kind": "Single",
        "executor_ref": {"agent_type": "bot", "agent_id": "bot_coding_001"},
        "objective": "整理 Context 协作协议评审结论并提交可评审 PR",
        "resource_refs": [
            {"resource_type": "forum_topic", "resource_id": "topic_001", "revision": "3"}
        ],
    }


def _append_body() -> dict:
    """spec §6.3 用户新增互动消息示例 (verbatim JSON, parent linked below)."""
    return {
        "request_id": "append-user-message-001",
        "expected_version": 1,
        "event_kind": "user_interaction.posted",
        "primitive_type": "Message",
        "actor_ref": {"principal_type": "human", "principal_id": "u_1001"},
        "parent_event": "evt_case_001",
        "visibility": "workspace",
        "payload": {"message_kind": "user_interaction", "content": "上下文做一下补充xxx"},
    }


_CASE_SNAPSHOT = {
    "objective": "整理 Context 协作协议评审结论并提交可评审 PR",
    "status": "created",
    "principal_ref": "u_1001",
    "executor_ref": "bot_coding_001",
    "method_kind": "Single",
    "progress": {
        "percent": 0,
        "completed_count": 0,
        "active_count": 0,
        "blocked_count": 0,
        "current_event_id": "evt_case_001",
    },
}


def _seed_create_happy(world) -> None:
    def create_context(self, _command, context):
        # The public face binds the L2 actor to the verified principal id and
        # marks the delivery source; anything else is an adapter regression.
        assert context.actor.principal_id == _CALLER
        assert context.source == "openapi"
        return ContextCollabCreatedResult(
            collab_context_id="collet_case_001",
            context_version=1,
            current_snapshot=dict(_CASE_SNAPSHOT),
            current_event={
                "event_id": "evt_case_001",
                "parent_event": None,
                "event_kind": "task.created",
                "primitive_type": "Task",
            },
            replayed=False,
        )

    bind_overrides(
        world, ContextCollabServiceProtocol, {"create_context": create_context},
    )


def _seed_append_happy(world) -> None:
    def append_event(self, _collab_context_id, command, _context):
        # actor_ref reconciliation already happened in the DTO; the bound actor
        # must be the authenticated principal, not the wire-declared one.
        assert command.actor_ref.principal_id == _CALLER
        return EventAcceptedResult(
            accepted=True,
            collab_context_id="collet_case_001",
            event_id="evt_case_002",
            parent_event="evt_case_001",
            event_kind="user_interaction.posted",
            primitive_type="Message",
            context_version=2,
            current_status="created",
        )

    bind_overrides(
        world, ContextCollabServiceProtocol, {"append_event": append_event},
    )


def _seed_append_conflict(world) -> None:
    def append_event(self, _collab_context_id, _command, _context):
        raise ContextCollabVersionConflict("case: stale expected_version")

    bind_overrides(
        world, ContextCollabServiceProtocol, {"append_event": append_event},
    )


def _seed_get_happy(world) -> None:
    def get_status(self, _collab_context_id, *, trunk_events, context):
        assert trunk_events is True  # query param must reach the service
        return ContextCollabStatusProjection(
            collab_context_id="collet_case_001",
            context_version=2,
            current_snapshot=dict(_CASE_SNAPSHOT),
            current_event={
                "event_id": "evt_case_002",
                "parent_event": "evt_case_001",
                "event_kind": "user_interaction.posted",
                "primitive_type": "Message",
                "status": "active",
                "summary": None,
                "content": "上下文做一下补充xxx",
                "occurred_at": None,
            },
            task_progress=TaskProgressProjectionDTO(
                total_events=2,
                events=[
                    {
                        "event_id": "evt_case_001",
                        "parent_event": None,
                        "event_kind": "task.created",
                        "primitive_type": "Task",
                        "status": "completed",
                        "summary": None,
                        "content": None,
                    },
                    {
                        "event_id": "evt_case_002",
                        "parent_event": "evt_case_001",
                        "event_kind": "user_interaction.posted",
                        "primitive_type": "Message",
                        "status": "active",
                        "summary": None,
                        "content": "上下文做一下补充xxx",
                    },
                ],
            ),
        )

    bind_overrides(
        world, ContextCollabServiceProtocol, {"get_status": get_status},
    )


def _seed_get_missing(world) -> None:
    def get_status(self, _collab_context_id, *, trunk_events, context):
        raise ContextCollabNotFoundError("case: unknown collab context")

    bind_overrides(
        world, ContextCollabServiceProtocol, {"get_status": get_status},
    )


# ── POST /context_collab(创建)──────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="authenticated_create",
    seed=lambda w: (_boot_verifier(w), _seed_create_happy(w)),
    input=CaseInput(headers=_headers(), json_body=_create_body()),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"collab_context_id": "collet_case_001", "context_version": 1},
        },
    ),
)
def create_authenticated():
    """Create binds ActorContext to the verified principal and envelopes the result."""
    pass


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="unauthenticated",
    seed=_boot_verifier,
    input=CaseInput(json_body=_create_body()),
    expect=ExpectError(status=401, json_contains={"code": 401000, "message": "Unauthorized"}),
)
def create_unauthenticated():
    pass


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="bad_principal_header",
    seed=_boot_verifier,
    input=CaseInput(headers=_BAD_HEADERS, json_body=_create_body()),
    expect=ExpectError(status=401, json_contains={"code": 401000, "message": "Unauthorized"}),
)
def create_bad_header():
    """A forged/undecodable header answers the same uniform 401 as no header."""
    pass


# ── POST /context_collab/{id}/events(追加事件)──────────────────────────────


_APPEND_PATH = f"{_BASE}/{{collab_context_id}}/events"


@endpoint_test(
    method="POST",
    path=_APPEND_PATH,
    scenario="authenticated_append",
    seed=lambda w: (_boot_verifier(w), _seed_append_happy(w)),
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_001"},
        headers=_headers(),
        json_body=_append_body(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"accepted": True, "context_version": 2},
        },
    ),
)
def append_authenticated():
    pass


@endpoint_test(
    method="POST",
    path=_APPEND_PATH,
    scenario="unauthenticated",
    seed=_boot_verifier,
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_001"},
        json_body=_append_body(),
    ),
    expect=ExpectError(status=401, json_contains={"code": 401000, "message": "Unauthorized"}),
)
def append_unauthenticated():
    pass


@endpoint_test(
    method="POST",
    path=_APPEND_PATH,
    scenario="version_conflict",
    seed=lambda w: (_boot_verifier(w), _seed_append_conflict(w)),
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_001"},
        headers=_headers(),
        json_body=_append_body(),
    ),
    expect=ExpectError(
        status=409,
        json_contains={"code": 409000, "message": "Context version conflict"},
    ),
)
def append_version_conflict():
    """Stale expected_version maps to the 409 ErrorEnvelope."""
    pass


@endpoint_test(
    method="POST",
    path=_APPEND_PATH,
    scenario="forged_actor_ref",
    seed=lambda w: _boot_verifier(w),
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_001"},
        headers=_headers(),
        json_body={
            **_append_body(),
            "actor_ref": {"principal_type": "human", "principal_id": "u_9999"},
        },
    ),
    expect=ExpectError(
        status=422,
        json_contains={"code": 422000, "message": "Invalid context collab request"},
    ),
)
def append_forged_actor_ref():
    """G1 twin-face rule: an actor_ref not matching the verified principal is 422."""
    pass


# ── GET /context_collab/{id}(查询)─────────────────────────────────────────


_GET_PATH = f"{_BASE}/{{collab_context_id}}"


@endpoint_test(
    method="GET",
    path=_GET_PATH,
    scenario="authenticated_status",
    seed=lambda w: (_boot_verifier(w), _seed_get_happy(w)),
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_001"},
        query_params={"trunk_events": True},
        headers=_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "collab_context_id": "collet_case_001",
                "current_snapshot": {"status": "created", "principal_ref": "u_1001"},
                "task_progress": {"total_events": 2},
            },
        },
    ),
)
def status_authenticated():
    pass


@endpoint_test(
    method="GET",
    path=_GET_PATH,
    scenario="unauthenticated",
    seed=_boot_verifier,
    input=CaseInput(path_params={"collab_context_id": "collet_case_001"}),
    expect=ExpectError(status=401, json_contains={"code": 401000, "message": "Unauthorized"}),
)
def status_unauthenticated():
    pass


@endpoint_test(
    method="GET",
    path=_GET_PATH,
    scenario="unknown_context",
    seed=lambda w: (_boot_verifier(w), _seed_get_missing(w)),
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_missing"},
        headers=_headers(),
    ),
    expect=ExpectError(
        status=404, json_contains={"code": 404000, "message": "Not found"},
    ),
)
def status_unknown_context():
    """Unknown collab context maps to the 404 ErrorEnvelope."""
    pass
