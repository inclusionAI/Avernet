"""Endpoint contracts for the authenticated Todo Space selector."""

from __future__ import annotations

from agentclaw.community.api.space_service import SpaceServiceProtocol
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/v1/collaboration/tasks/spaces"
_ACTOR_ID = "todo-space-actor"
_HEADERS = {"x-user-id": _ACTOR_ID}


def _seed_personal_space(world) -> None:
    world.get(SpaceServiceProtocol).initialize_personal(
        user_id=_ACTOR_ID,
        create_sc_team=False,
    )


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="lists_authenticated_users_accessible_spaces",
    seed=_seed_personal_space,
    input=CaseInput(
        headers=_HEADERS,
        query_params={
            "user_id": "spoofed-user",
            "page_no": 1,
            "page_size": 100,
            "scope": "accessible",
        },
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 1,
                "items": [
                    {
                        "space_type": "PERSONAL",
                    }
                ],
            },
        },
    ),
)
def list_task_spaces_ok():
    """List only Spaces visible to the verified login actor."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="rejects_non_accessible_scope",
    input=CaseInput(
        headers=_HEADERS,
        query_params={"scope": "all"},
    ),
    expect=ExpectError(status=422),
)
def list_task_spaces_invalid_scope():
    """The Todo selector cannot request the unrestricted Space scope."""
