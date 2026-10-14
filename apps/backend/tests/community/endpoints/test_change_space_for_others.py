"""Endpoint-framework coverage for the admin batch Bot Space change."""

from __future__ import annotations

from agentclaw.community.api.space_service import SpaceServiceProtocol
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_ADMIN = "100000"  # seeded super_admin in application-test.yaml
_OWNER = "change-space-for-others-owner"
_TEAM_SPACE_ID = 1


def _seed_admin_owner_and_team_space(world) -> None:
    """An admin whose target owner owns a Bot and a joinable team Space."""
    make_staff_user(world, user_id=_ADMIN)
    make_staff_user(world, user_id=_OWNER)
    make_bot(
        world,
        bot_id="bot_space_batch_member",
        owner_id=_OWNER,
        bot_type="personal",
        status="ACTIVE",
    )
    world.get(SpaceServiceProtocol).create_team(
        name="Space Batch Team", creator_id=_OWNER
    )


@endpoint_test(
    method="POST",
    path="/api/bots/change-space-for-others",
    scenario="ok_batch_moves_bots",
    seed=_seed_admin_owner_and_team_space,
    input=CaseInput(
        headers={"x-user-id": _ADMIN},
        json_body={
            "space_id": _TEAM_SPACE_ID,
            "bot_list": [{"owner_id": _OWNER, "bot_id": "bot_space_batch_member"}],
        },
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"total": 1, "succeeded": 1, "failed": 0},
        },
    ),
)
def change_space_for_others_ok():
    """A super-admin batch-moves owned Bots into the joined team Space."""


@endpoint_test(
    method="POST",
    path="/api/bots/change-space-for-others",
    scenario="not_admin",
    seed=_seed_admin_owner_and_team_space,
    input=CaseInput(
        headers={"x-user-id": _OWNER},
        json_body={
            "space_id": _TEAM_SPACE_ID,
            "bot_list": [{"owner_id": _OWNER, "bot_id": "bot_space_batch_member"}],
        },
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 403},
    ),
)
def change_space_for_others_not_admin():
    """A non-admin caller is refused before any Bot is read."""