"""Authorization tests for the user-management endpoints."""
from __future__ import annotations

from tests.community.factories.access import make_staff_user
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


def _seed_list_user(world) -> None:
    make_staff_user(world, user_id="listed-user")


@endpoint_test(
    method="GET",
    path="/api/v1/user",
    scenario="operator_can_list",
    input=CaseInput(
        query_params={"user_type": "staff"},
        headers={"x-user-id": "operator-user"},
    ),
    seed=_seed_list_user,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def operator_can_list_users():
    """Existing operator list behavior remains unchanged."""


@endpoint_test(
    method="GET",
    path="/api/v1/user",
    scenario="unauthenticated",
    input=CaseInput(),
    expect=ExpectError(status=401),
)
def list_users_requires_authentication():
    """An unauthenticated caller cannot enumerate user records."""


@endpoint_test(
    method="GET",
    path="/api/v1/user/{user_type}/{user_id}",
    scenario="unauthenticated",
    input=CaseInput(
        path_params={"user_type": "COMPETE", "user_id": "another-user"},
    ),
    expect=ExpectError(status=401),
)
def get_user_requires_authentication():
    """An unauthenticated caller cannot read another user's record."""


@endpoint_test(
    method="POST",
    path="/api/v1/user",
    scenario="operator_can_upsert",
    input=CaseInput(
        json_body={
            "user_id": "managed-user",
            "user_type": "COMPETE",
            "status": "ACCESS",
        },
        headers={"x-user-id": "operator-user"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"userId": "managed-user"}},
    ),
)
def operator_can_upsert_user():
    """Existing operator upsert behavior remains unchanged."""


@endpoint_test(
    method="POST",
    path="/api/v1/user",
    scenario="unauthenticated",
    input=CaseInput(
        json_body={
            "user_id": "another-user",
            "user_type": "COMPETE",
            "status": "ACCESS",
        },
    ),
    expect=ExpectError(status=401),
)
def upsert_user_requires_authentication():
    """An unauthenticated caller cannot create or modify user records."""
