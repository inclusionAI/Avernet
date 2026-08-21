"""Endpoint coverage for work orders and recipient notifications.

One seed powers the owner-side cases: the acting user creates a team Space
(Space id 1 in the fresh per-case database) and another user files a join
request through the real service, which writes work order 1 and — one row
per Space owner — notification 1 for the acting user. The join-request case
inverts the roles: someone else owns the Space and the acting user applies.
The uniform error path is the principal seam's 403 on a ``user_id`` naming
someone other than the verified caller.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.api.space_service import SpaceServiceProtocol
from agentclaw.community.api.work_order_service import WorkOrderServiceProtocol
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_USER_ID = "work-orders-endpoint-user"
_APPLICANT_ID = "work-orders-endpoint-applicant"
_OTHER_OWNER_ID = "work-orders-endpoint-owner"
_SIGNING_KEY = "work-orders-endpoint-secret-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal_headers() -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60 * 60,
            "principals": [
                {
                    "type": "user",
                    "tenant": "work-orders-endpoint-test",
                    "subject": {
                        "id": _USER_ID,
                        "username": "work-orders-endpoint-user@example.com",
                    },
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def _enable_public_auth(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_pending_request_for_me(world) -> None:
    """The acting user owns Space 1; someone else's join request is pending.

    Writes work order 1 and notification 1 (recipient: the acting user).
    """
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).create_team(
        name="Work Order Team", creator_id=_USER_ID
    )
    world.get(WorkOrderServiceProtocol).create_space_join_request(
        space_id=1,
        applicant_user_id=_APPLICANT_ID,
        reason="please let me join",
    )


def _seed_joinable_space(world) -> None:
    """A team Space owned by someone else, for the acting user to apply to."""
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).create_team(
        name="Joinable Team", creator_id=_OTHER_OWNER_ID
    )


def _mismatched_user(path_params: dict | None = None, json_body: dict | None = None):
    """The uniform error case: naming someone other than the caller is a 403."""
    return CaseInput(
        path_params=path_params or {},
        query_params={"user_id": "someone-else"},
        json_body=json_body,
        headers=_principal_headers(),
    )


# ── POST /openapi/v1/bots/spaces/{space_id}/join-requests ─────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/join-requests",
    scenario="happy",
    seed=_seed_joinable_space,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        json_body={"reason": "please let me join"},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {"work_order_id": 1, "status": "PENDING"},
        },
    ),
)
def create_space_join_request_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/join-requests",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        path_params={"space_id": 1}, json_body={"reason": "please let me join"}
    ),
    expect=ExpectError(status=403),
)
def create_space_join_request_wrong_user():
    """The framework owns invocation."""


# ── GET /openapi/v1/bots/work-orders ──────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/work-orders",
    scenario="happy",
    seed=_seed_pending_request_for_me,
    input=CaseInput(
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"total": 1}}
    ),
)
def list_work_orders_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/work-orders",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(),
    expect=ExpectError(status=403),
)
def list_work_orders_wrong_user():
    """The framework owns invocation."""


# ── GET /openapi/v1/bots/work-orders/{work_order_id} ──────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/work-orders/{work_order_id}",
    scenario="happy",
    seed=_seed_pending_request_for_me,
    input=CaseInput(
        path_params={"work_order_id": 1},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"work_order_id": 1, "status": "PENDING", "can_approve": True},
        },
    ),
)
def get_work_order_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/work-orders/{work_order_id}",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(path_params={"work_order_id": 1}),
    expect=ExpectError(status=403),
)
def get_work_order_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/work-orders/{work_order_id}/approve ─────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/work-orders/{work_order_id}/approve",
    scenario="happy",
    seed=_seed_pending_request_for_me,
    input=CaseInput(
        path_params={"work_order_id": 1},
        query_params={"user_id": _USER_ID},
        json_body={"review_remark": "welcome aboard"},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"work_order_id": 1, "status": "APPROVED"},
        },
    ),
)
def approve_work_order_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/work-orders/{work_order_id}/approve",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        path_params={"work_order_id": 1}, json_body={"review_remark": "welcome"}
    ),
    expect=ExpectError(status=403),
)
def approve_work_order_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/work-orders/{work_order_id}/reject ──────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/work-orders/{work_order_id}/reject",
    scenario="happy",
    seed=_seed_pending_request_for_me,
    input=CaseInput(
        path_params={"work_order_id": 1},
        query_params={"user_id": _USER_ID},
        json_body={"review_remark": "not this time"},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"work_order_id": 1, "status": "REJECTED"},
        },
    ),
)
def reject_work_order_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/work-orders/{work_order_id}/reject",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        path_params={"work_order_id": 1}, json_body={"review_remark": "no"}
    ),
    expect=ExpectError(status=403),
)
def reject_work_order_wrong_user():
    """The framework owns invocation."""


# ── GET /openapi/v1/bots/work-order-notifications/unread-count ────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/work-order-notifications/unread-count",
    scenario="happy",
    seed=_seed_pending_request_for_me,
    input=CaseInput(
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "unread_count": 1,
                "pending_approval_count": 1,
                "unread_notice_count": 0,
                "badge_count": 1,
            },
        },
    ),
)
def unread_notification_count_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/work-order-notifications/unread-count",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(),
    expect=ExpectError(status=403),
)
def unread_notification_count_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/work-order-notifications/read-all ───────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/work-order-notifications/read-all",
    scenario="happy",
    seed=_seed_pending_request_for_me,
    input=CaseInput(
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"updated_count": 1}}
    ),
)
def mark_all_notifications_read_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/work-order-notifications/read-all",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(),
    expect=ExpectError(status=403),
)
def mark_all_notifications_read_wrong_user():
    """The framework owns invocation."""


# ── GET /openapi/v1/bots/work-order-notifications/{notification_id} ───────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/work-order-notifications/{notification_id}",
    scenario="happy",
    seed=_seed_pending_request_for_me,
    input=CaseInput(
        path_params={"notification_id": 1},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            # Fetching the detail marks the notification read — asserted so a
            # regression in that side effect is visible here.
            "data": {"notification_id": 1, "work_order_id": 1, "is_read": True},
        },
    ),
)
def get_notification_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/work-order-notifications/{notification_id}",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(path_params={"notification_id": 1}),
    expect=ExpectError(status=403),
)
def get_notification_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/work-order-notifications/{notification_id}/read ─────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/work-order-notifications/{notification_id}/read",
    scenario="happy",
    seed=_seed_pending_request_for_me,
    input=CaseInput(
        path_params={"notification_id": 1},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"notification_id": 1, "is_read": True}},
    ),
)
def mark_notification_read_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/work-order-notifications/{notification_id}/read",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(path_params={"notification_id": 1}),
    expect=ExpectError(status=403),
)
def mark_notification_read_wrong_user():
    """The framework owns invocation."""
