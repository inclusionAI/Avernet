"""Endpoint coverage for the internal Installation backfill routes.

Every case is decided by real state in the per-test database: the bot rows
the sweep pages over, and the SkillSet configuration the flush reconciles
against. Nothing is mocked — the request reaches the real
``CapabilityDesiredStateRepository.flush_installations``, so a green case
here means the whole path (token → service → flush → report) works.

A seeded bot with no SkillSets converges to ``changed: false``: its
Installation already agrees with what no Set asks for. That is the correct
answer, and it is what makes the report trustworthy — the backfill claims a
write only when it made one.
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
            "data": {
                "bot_id": _BOT_ID,
                "owner_id": _OWNER_ID,
                "changed": False,
                "error": None,
            },
        },
    ),
)
def backfill_bot_ok():
    """Happy path: the flush runs and reports that it wrote nothing."""


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


@endpoint_test(
    method="POST",
    path="/api/internal/skill-center/installations/backfill/page",
    scenario="ok",
    input=CaseInput(headers=_AUTH_HEADERS, json_body={"page": 1, "page_size": 50}),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "page": 1,
                "page_size": 50,
                "failed": 0,
                "has_more": False,
                # The sweep reached the seeded Bot rather than skipping it.
                "outcomes": [{"bot_id": _BOT_ID, "changed": False, "error": None}],
            },
        },
    ),
)
def backfill_page_ok():
    """Happy path: an unfiltered page sweeps the env's Bots and reports each."""


@endpoint_test(
    method="POST",
    path="/api/internal/skill-center/installations/backfill/page",
    scenario="invalid_page_size",
    input=CaseInput(headers=_AUTH_HEADERS, json_body={"page_size": 0}),
    expect=ExpectError(status=422),
)
def backfill_page_invalid_page_size():
    """A page size outside [1, 200] is refused before any Bot is touched."""
