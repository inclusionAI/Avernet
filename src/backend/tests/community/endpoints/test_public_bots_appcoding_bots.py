"""Endpoint test for GET /api/public/bots/{bot_id}/appcoding-bots (no auth).

Exercises the real handler → real ``BotService`` → real repositories against the
per-test SQLite DB: a coding bot and its template (linked to the architect bot
via ``ext.architect_bot_id``) are inserted directly, then the public endpoint is
expected to return the coding bot. No service mocking — the previous version
patched ``BotService.list_coding_bots_by_architect`` at the class level without
stopping the patcher, leaking a "Database error" stub into later order-dependent
tests. (The contrived service-raises-500 case was dropped with that mock.)
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot import TemplateRepository

from tests.community.framework import (
    CaseInput,
    ExpectSuccess,
    endpoint_test,
)

ARCHITECT_BOT_ID = "arch_001"
CODING_BOT_ID = "app_bot_1"


def _seed_appcoding_bot(world):
    """Insert a real coding bot + a template linking it to the architect bot."""
    world.get(BotRepository).insert(
        {
            "bot_id": CODING_BOT_ID,
            "bot_name": "App Coding Bot 1",
            "owner_id": "test_user",
            "owner_name": "test_user",
            "creator_id": "test_user",
            "entity_id": "test_user",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "status": "ACTIVE",
        }
    )
    world.get(TemplateRepository).insert(
        {
            "bot_id": CODING_BOT_ID,
            "ext": {"architect_bot_id": ARCHITECT_BOT_ID, "template": "appcoding"},
        }
    )


@endpoint_test(
    method="GET",
    path="/api/public/bots/{bot_id}/appcoding-bots",
    scenario="ok",
    input=CaseInput(
        path_params={"bot_id": ARCHITECT_BOT_ID},
    ),
    seed=_seed_appcoding_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": [{"bot_id": CODING_BOT_ID}],
        },
    ),
)
def list_public_coding_bots_ok():
    """Happy path: a coding bot linked to the architect bot (no auth required)."""


# --- Regression: members (collaborators) are returned on each coding bot ---

from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol

MEMBER_USER_ID = "collab_001"
MEMBER_USER_NAME = "Collab One"
MEMBER_ROLE = "member"


def _seed_appcoding_bot_with_member(world):
    """Insert a coding bot, its template, and one collaborator (member)."""
    bot = world.get(BotRepository).insert(
        {
            "bot_id": CODING_BOT_ID,
            "bot_name": "App Coding Bot 1",
            "owner_id": "test_user",
            "owner_name": "test_user",
            "creator_id": "test_user",
            "entity_id": "test_user",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "status": "ACTIVE",
        }
    )
    world.get(TemplateRepository).insert(
        {
            "bot_id": CODING_BOT_ID,
            "ext": {"architect_bot_id": ARCHITECT_BOT_ID, "template": "appcoding"},
        }
    )
    # Seed a collaborator (member) linked via bot_pk (ac_bots.id).
    world.get(CollaboratorRepositoryProtocol).insert(
        {
            "bot_pk": bot["id"],
            "bot_id": CODING_BOT_ID,
            "owner_id": "test_user",
            "user_id": MEMBER_USER_ID,
            "user_name": MEMBER_USER_NAME,
            "role": MEMBER_ROLE,
            "operator_id": "test_user",
        }
    )


def _assert_members_returned(response, world):
    """extra_assertion: each returned coding bot carries its member list.

    Runner calls extra_assertions as ``assertion(response, world)``.
    """
    items = response.json().get("data", [])
    coding = next(it for it in items if it.get("bot_id") == CODING_BOT_ID)
    members = coding.get("members")
    assert isinstance(members, list), "members should be a list"
    assert len(members) == 1, f"expected 1 member, got {len(members)}"
    m = members[0]
    assert m["user_id"] == MEMBER_USER_ID
    assert m["user_name"] == MEMBER_USER_NAME
    # operator_id / role / timestamps are intentionally NOT exposed here.
    for hidden in ("operator_id", "role", "gmt_create", "gmt_modified"):
        assert hidden not in m, f"{hidden} should not be exposed"


@endpoint_test(
    method="GET",
    path="/api/public/bots/{bot_id}/appcoding-bots",
    scenario="ok-with-members",
    input=CaseInput(
        path_params={"bot_id": ARCHITECT_BOT_ID},
    ),
    seed=_seed_appcoding_bot_with_member,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": [{"bot_id": CODING_BOT_ID}],
        },
    ),
    extra_assertions=(_assert_members_returned,),
)
def list_public_coding_bots_with_members():
    """Each coding bot now returns its `members` (collaborators) list."""
