"""Endpoint coverage for the internal Installation backfill route.

Both cases are decided by real state in the per-test database: the bot row the
flush is scoped to, and the SkillSet configuration it reconciles against.
Nothing is mocked — the request reaches the real
``CapabilityDesiredStateRepository.flush_installations``, so a green case here
means the whole path (token → service → flush) works.
"""

from __future__ import annotations

from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

# The token SkillCenterInternalTokenBindings falls back to when no secret
# name is configured — which is the case in the test profile.
_AUTH_HEADERS = {"Authorization": "Bearer singlebox-skill-center-token-local"}
_BOT_ID = "bot-backfill"
_OWNER_ID = "owner-backfill"


def _seed_bot(world) -> None:
    make_staff_user(world, user_id=_OWNER_ID)
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_OWNER_ID,
        owner_name=_OWNER_ID,
        bot_type="personal",
        status="ACTIVE",
    )


@endpoint_test(
    method="POST",
    path="/api/internal/skill-center/installations/backfill/bot",
    scenario="ok",
    input=CaseInput(
        headers=_AUTH_HEADERS,
        json_body={"bot_id": _BOT_ID, "owner_id": _OWNER_ID},
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"bot_id": _BOT_ID, "owner_id": _OWNER_ID},
        },
    ),
)
def backfill_bot_ok():
    """Happy path: the flush runs against the seeded Bot."""


@endpoint_test(
    method="POST",
    path="/api/internal/skill-center/installations/backfill/bot",
    scenario="not_found",
    input=CaseInput(
        headers=_AUTH_HEADERS,
        json_body={"bot_id": "no-such-bot", "owner_id": _OWNER_ID},
    ),
    seed=_seed_bot,
    expect=ExpectError(status=404, json_contains={"detail": "Bot not found"}),
)
def backfill_bot_not_found():
    """A Bot that does not exist for this owner must not read as converged."""
