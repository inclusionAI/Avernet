"""Endpoint test for GET /api/bots/{bot_id}/appcoding-bots.

Exercises the real handler → real ``BotService`` → real repositories against the
per-test SQLite DB: a coding bot and its template (linked to the architect bot
via ``ext.architect_bot_id``) are inserted directly, then the endpoint is
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
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

ARCHITECT_BOT_ID = "default"
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
    path="/api/bots/{bot_id}/appcoding-bots",
    scenario="ok",
    input=CaseInput(
        path_params={"bot_id": ARCHITECT_BOT_ID},
        headers={"x-user-id": "test_user"},
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
def list_appcoding_bots_ok():
    """Happy path: a coding bot linked to the architect bot is returned."""


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/appcoding-bots",
    scenario="unauthenticated",
    input=CaseInput(
        path_params={"bot_id": ARCHITECT_BOT_ID},
        # No x-user-id header → the auth dependency rejects with 401.
    ),
    expect=ExpectError(status=401),
)
def list_appcoding_bots_unauthenticated():
    """Error path: no authenticated user → 401 (real auth guard, no mock)."""
