"""Endpoint tests for PATCH /api/public/bots/{bot_id}/ext.

Seeds real rows through ``BotRepository.insert`` so the request hits the
same SQLAlchemy code paths the production handler does — no mocking of
injected components. The happy path verifies the whitelisted-field
update round-trips through the DB; the error path drives the
not-found branch by simply not seeding.
"""
from __future__ import annotations

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.world import World


def _seed_bot(world: World) -> None:
    """Insert a real bot row so ``list_by_conditions`` returns it."""
    repo = world.get(BotRepository)
    repo.insert(
        {
            "bot_id": "test_bot",
            "bot_name": "test_bot",
            "entity_id": "test_owner",
            "entity_type": "user",
            "creator_id": "test_owner",
            "owner_id": "test_owner",
            "ext": {"existing_key": "existing_value"},
        }
    )


def _assert_ext_persisted(response, world: World) -> None:
    """The whitelisted update must be visible on a subsequent read."""
    repo = world.get(BotRepository)
    _, items = repo.list_by_conditions(bot_id="test_bot", page=1, page_size=1)
    assert items, "seeded bot should still exist after PATCH"
    ext = items[0].get("ext") or {}
    assert ext.get("is_domain_bot") is True
    assert ext.get("arch_domain") == "新架构域"
    # Whitelist must preserve existing non-whitelisted keys.
    assert ext.get("existing_key") == "existing_value"


@endpoint_test(
    method="PATCH",
    path="/api/public/bots/{bot_id}/ext",
    scenario="ok",
    input=CaseInput(
        path_params={"bot_id": "test_bot"},
        json_body={"is_domain_bot": True, "arch_domain": "新架构域"},
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"bot_id": "test_bot"},
        },
    ),
    extra_assertions=(_assert_ext_persisted,),
)
def update_bot_ext_public_ok():
    """Happy path: whitelisted ext fields are persisted via the repo."""


@endpoint_test(
    method="PATCH",
    path="/api/public/bots/{bot_id}/ext",
    scenario="not_found",
    input=CaseInput(
        path_params={"bot_id": "missing_bot"},
        json_body={"is_domain_bot": True},
    ),
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 404,
        },
    ),
)
def update_bot_ext_public_not_found():
    """Error path: no seeded bot → repo returns empty → 404 envelope."""
