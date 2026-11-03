"""Endpoint coverage for GET /api/public/bots/{bot_id}/overview."""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


BOT_ID = "public_overview_bot"
PATH = "/api/public/bots/{bot_id}/overview"


def _seed_bot(world) -> None:
    world.get(BotRepository).insert(
        {
            "bot_id": BOT_ID,
            "bot_name": "Public Overview Bot",
            "owner_id": "owner_overview",
            "owner_name": "Owner Overview",
            "creator_id": "owner_overview",
            "entity_id": "owner_overview",
            "entity_type": "staff",
            "active_engine": "claude_code",
            "bot_type": "personal",
            "status": "ACTIVE",
            "device_id": "device_overview",
        }
    )


def _assert_no_user_data(response, _world) -> None:
    body = response.text
    assert "owner_overview" not in body
    assert "device_overview" not in body


@endpoint_test(
    method="GET",
    path=PATH,
    scenario="happy",
    input=CaseInput(path_params={"bot_id": BOT_ID}),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "bot_id": BOT_ID,
            "engine": "claude_code",
            "status": "ACTIVE",
            "payload": {"engine": "claude_code"},
        },
    ),
    extra_assertions=(_assert_no_user_data,),
)
def get_public_bot_overview_happy():
    """AICoding-family Bot gets the engine payload; no owner or device leaks."""


@endpoint_test(
    method="GET",
    path=PATH,
    scenario="not_found",
    input=CaseInput(path_params={"bot_id": "missing_overview_bot"}),
    expect=ExpectError(status=404),
)
def get_public_bot_overview_not_found():
    """Unknown Bot is a 404."""


@endpoint_test(
    method="GET",
    path=PATH,
    scenario="activity_window_out_of_range",
    input=CaseInput(
        path_params={"bot_id": BOT_ID},
        query_params={"activity_days": "0"},
    ),
    seed=_seed_bot,
    expect=ExpectError(status=422),
)
def get_public_bot_overview_bad_window():
    """The session-activity window is bounded."""
