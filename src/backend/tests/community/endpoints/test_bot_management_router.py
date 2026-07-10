"""Smoke tests for bot management endpoints.

Tests the following endpoints from ``adapters/http/bot_management/router.py``:
- GET /api/bots/by-owner-or-collaborator
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentclaw.community.adapters.http.bot_management.router import (
    _build_bots_by_owner_or_collaborator_data,
    list_bots_by_owner_or_collaborator as list_bots_route,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot, make_collaborator
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


# ============================================================================
# Test Setup: seed users, bots, and collaborators
# ============================================================================


def _seed_owner_with_bots(world):
    """Seed a bot owner with multiple owned bots."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_owned_1", owner_id="u_owner", bot_type="service", status="ACTIVE")
    make_bot(world, bot_id="bot_owned_2", owner_id="u_owner", bot_type="desktop", status="ACTIVE")


def _seed_collaborator_with_bots(world):
    """Seed a user who is a collaborator on another user's bot."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_collab")
    make_bot(world, bot_id="bot_other", owner_id="u_owner", bot_type="service", status="ACTIVE")
    make_collaborator(
        world,
        bot_id="bot_other",
        owner_id="u_owner",
        user_id="u_collab",
        role="member",
        operator_id="u_owner",
    )


def _seed_owner_and_collaborator_bots(world):
    """Seed a user who owns bots AND is collaborator on other bots."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_other")
    make_staff_user(world, user_id="u_collab")
    # u_collab owns a bot
    make_bot(world, bot_id="bot_owned", owner_id="u_collab", bot_type="service", status="ACTIVE")
    # u_other owns a bot where u_collab is collaborator
    make_bot(world, bot_id="bot_collab", owner_id="u_other", bot_type="service", status="ACTIVE")
    make_collaborator(
        world,
        bot_id="bot_collab",
        owner_id="u_other",
        user_id="u_collab",
        role="admin",
        operator_id="u_other",
    )


def _seed_empty_user(world):
    """Seed a user with no bots and no collaborations."""
    make_staff_user(world, user_id="u_empty")


def _seed_multiple_bots_with_statuses(world):
    """Seed bots with various statuses."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_active", owner_id="u_owner", bot_type="service", status="ACTIVE")
    make_bot(world, bot_id="bot_pending", owner_id="u_owner", bot_type="service", status="PENDING")
    make_bot(world, bot_id="bot_inactive", owner_id="u_owner", bot_type="service", status="INACTIVE")


# ============================================================================
# Extra Assertions
# ============================================================================


def _assert_list_contains_owned_bots(response, world):
    """Assert that list response contains owned bots."""
    data = response.json()["data"]
    items = data.get("items", [])
    assert len(items) >= 2, f"Expected at least 2 owned bots, got {len(items)}"
    bot_ids = {b["bot_id"] for b in items}
    assert "bot_owned_1" in bot_ids, f"Expected bot_owned_1 in {bot_ids}"
    assert "bot_owned_2" in bot_ids, f"Expected bot_owned_2 in {bot_ids}"


def _assert_list_contains_collab_bot(response, world):
    """Assert that list response contains bot where user is collaborator."""
    data = response.json()["data"]
    items = data.get("items", [])
    assert len(items) >= 1, f"Expected at least 1 bot, got {len(items)}"
    bot_ids = {b["bot_id"] for b in items}
    assert "bot_other" in bot_ids, f"Expected bot_other in {bot_ids}"


def _assert_list_contains_both_owned_and_collab(response, world):
    """Assert that list response contains both owned and collaborator bots."""
    data = response.json()["data"]
    items = data.get("items", [])
    assert len(items) >= 2, f"Expected at least 2 bots, got {len(items)}"
    bot_ids = {b["bot_id"] for b in items}
    assert "bot_owned" in bot_ids, f"Expected bot_owned in {bot_ids}"
    assert "bot_collab" in bot_ids, f"Expected bot_collab in {bot_ids}"


def _assert_list_empty(response, world):
    """Assert that list response is empty."""
    data = response.json()["data"]
    items = data.get("items", [])
    assert len(items) == 0, f"Expected empty list, got {len(items)} items"
    assert data.get("total") == 0, f"Expected total=0, got {data.get('total')}"


def _assert_list_has_required_fields(response, world):
    """Assert that each bot in the list has required fields."""
    data = response.json()["data"]
    items = data.get("items", [])
    if len(items) > 0:
        bot = items[0]
        required_fields = ["bot_id", "owner_id", "bot_name", "status"]
        for field in required_fields:
            assert field in bot, f"Bot should have {field}, got keys: {list(bot.keys())}"


def _assert_default_bot_is_first_owned(response, world):
    """Assert that default_bot is set to the first bot if available."""
    data = response.json()["data"]
    default_bot = data.get("default_bot")
    items = data.get("items", [])
    if items:
        assert default_bot is not None, "Expected default_bot to be set when items exist"
        assert "bot_id" in default_bot, "default_bot should have bot_id"
        assert "entity_id" in default_bot, "default_bot should have entity_id"


def _assert_response_has_total_and_items(response, world):
    """Assert that response has total and items fields."""
    data = response.json()["data"]
    assert "total" in data, "Response should have 'total' field"
    assert "items" in data, "Response should have 'items' field"
    assert isinstance(data["items"], list), "'items' should be a list"


# ============================================================================
# GET /api/bots/by-owner-or-collaborator
# ============================================================================


def test_owner_or_collaborator_list_defaults_null_engine_types():
    class BotServiceWithNullEngineTypes:
        def list_bots_by_owner_or_collaborator(self, **kwargs):
            return {
                "total": 1,
                "items": [
                    {
                        "bot_id": "bot-1",
                        "entity_id": "entity-1",
                        "entity_type": "staff",
                        "engine_types": None,
                        "active_engine": "openclaw",
                    }
                ],
            }

        def get_engine_paths(
            self,
            entity_id,
            bot_id,
            engine_types,
            entity_type,
        ):
            return {
                engine: f"/tmp/{bot_id}/{engine}"
                for engine in engine_types
            }

    with patch(
        "agentclaw.community.adapters.http.bot_management.router._get_engine_types",
        return_value=["openclaw"],
    ):
        data = _build_bots_by_owner_or_collaborator_data(
            owner_id="u",
            bot_service=BotServiceWithNullEngineTypes(),
        )

    assert data["items"][0]["engine_paths"] == {
        "openclaw": "/tmp/bot-1/openclaw"
    }


@pytest.mark.asyncio
async def test_owner_or_collaborator_list_does_not_block_event_loop():
    release_path_build = threading.Event()
    fallback_release = threading.Timer(0.5, release_path_build.set)

    class SlowBotService:
        def list_bots_by_owner_or_collaborator(self, **kwargs):
            return {
                "total": 1,
                "items": [
                    {
                        "bot_id": "bot-1",
                        "entity_id": "entity-1",
                        "entity_type": "staff",
                        "engine_types": ["openclaw"],
                        "active_engine": "openclaw",
                    }
                ],
            }

        def get_engine_paths(self, *args):
            release_path_build.wait(timeout=1)
            return {"openclaw": "/tmp/bot-1/openclaw"}

    loop = asyncio.get_running_loop()
    started = loop.time()
    task = asyncio.create_task(
        list_bots_route(
            ctx=SimpleNamespace(user_id="u"),
            bot_service=SlowBotService(),
        )
    )

    try:
        fallback_release.start()
        await asyncio.sleep(0)
        heartbeat_delay = loop.time() - started
        assert heartbeat_delay < 0.25
    finally:
        release_path_build.set()
        fallback_release.cancel()
        response = await task

    assert response.success is True


@endpoint_test(
    method="GET",
    path="/api/bots/by-owner-or-collaborator",
    scenario="ok_owned_bots",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_owner_with_bots,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(
        _assert_list_contains_owned_bots,
        _assert_list_has_required_fields,
        _assert_response_has_total_and_items,
        _assert_default_bot_is_first_owned,
    ),
)
def list_bots_by_owner_or_collaborator_owned():
    """List bots returns success with owned bots and all required fields."""


@endpoint_test(
    method="GET",
    path="/api/bots/by-owner-or-collaborator",
    scenario="ok_collab_bot",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
    ),
    seed=_seed_collaborator_with_bots,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(
        _assert_list_contains_collab_bot,
        _assert_list_has_required_fields,
    ),
)
def list_bots_by_owner_or_collaborator_collab():
    """List bots returns success with bots where user is collaborator."""


@endpoint_test(
    method="GET",
    path="/api/bots/by-owner-or-collaborator",
    scenario="ok_both_owned_and_collab",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
    ),
    seed=_seed_owner_and_collaborator_bots,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(
        _assert_list_contains_both_owned_and_collab,
        _assert_list_has_required_fields,
    ),
)
def list_bots_by_owner_or_collaborator_mixed():
    """List bots returns both owned and collaborator bots."""


@endpoint_test(
    method="GET",
    path="/api/bots/by-owner-or-collaborator",
    scenario="empty_no_bots",
    input=CaseInput(
        headers={"x-user-id": "u_empty"},
    ),
    seed=_seed_empty_user,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(
        _assert_list_empty,
        _assert_response_has_total_and_items,
    ),
)
def list_bots_by_owner_or_collaborator_empty():
    """List bots with no owned or collaborator bots returns empty list."""


@endpoint_test(
    method="GET",
    path="/api/bots/by-owner-or-collaborator",
    scenario="ok_various_statuses",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_multiple_bots_with_statuses,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(
        _assert_response_has_total_and_items,
        _assert_list_has_required_fields,
    ),
)
def list_bots_by_owner_or_collaborator_various_statuses():
    """List bots returns bots with various statuses."""


def _seed_with_service_error(world):
    """Seed: mock bot service to raise an exception."""
    from agentclaw.community.core.bot_management.services.bot_service import BotService

    def mock_list_bots_by_owner_or_collaborator(*args, **kwargs):
        raise RuntimeError("Mock service error")

    patcher = patch.object(
        BotService,
        "list_bots_by_owner_or_collaborator",
        mock_list_bots_by_owner_or_collaborator,
    )
    patcher.start()
    world._bot_list_service_error_patcher = patcher


@endpoint_test(
    method="GET",
    path="/api/bots/by-owner-or-collaborator",
    scenario="service_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_with_service_error,
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
        },
    ),
)
def list_bots_by_owner_or_collaborator_service_error():
    """List bots handles service errors gracefully."""


# ============================================================================
# GET /api/bots/ceiling
# ============================================================================


def _seed_user_for_ceiling(world):
    """Seed a plain staff user; PolicyService falls back to the default ceiling."""
    make_staff_user(world, user_id="u_owner")


def _assert_ceiling_is_int(response, world):
    """Assert ``data.ceiling`` is present and an integer."""
    data = response.json()["data"]
    assert "ceiling" in data, f"Expected 'ceiling' in data, got keys: {list(data.keys())}"
    assert isinstance(data["ceiling"], int), f"Expected int ceiling, got {type(data['ceiling'])}"


@endpoint_test(
    method="GET",
    path="/api/bots/ceiling",
    scenario="ok_default_ceiling",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_user_for_ceiling,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_ceiling_is_int,),
)
def get_bots_ceiling_ok():
    """GET ceiling returns success with an integer ceiling value."""


def _seed_ceiling_service_error(world):
    """Patch PolicyService.get_bots_ceiling to raise, exercising the except branch."""
    from agentclaw.community.core.access.services.policy_service import PolicyService

    def mock_get_bots_ceiling(*args, **kwargs):
        raise RuntimeError("Mock policy service error")

    patcher = patch.object(PolicyService, "get_bots_ceiling", mock_get_bots_ceiling)
    patcher.start()
    world._get_bots_ceiling_error_patcher = patcher


@endpoint_test(
    method="GET",
    path="/api/bots/ceiling",
    scenario="service_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
    ),
    seed=_seed_ceiling_service_error,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def get_bots_ceiling_service_error():
    """GET ceiling returns success=False, error_code=500 when the service raises."""
