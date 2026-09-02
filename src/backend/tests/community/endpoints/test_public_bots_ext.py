"""Endpoint tests for PATCH /api/public/bots/{bot_id}/ext.

Seeds real rows through ``BotRepository.insert`` so the request hits the
same SQLAlchemy code paths the production handler does — no mocking of
injected components. The happy path verifies the whitelisted-field
update round-trips through the DB; the error path drives the
not-found branch by simply not seeding.

The route writes another owner's bot, so it is admin-gated: cases carry
``x-user-id`` for the seeded ``super_admin``. Two authorization legs are
pinned here because they need the real auth dependency — an anonymous
request (401) and a logged-in non-admin (403 envelope).
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.world import World

ADMIN_STAFF_ID = "100000"  # seeded super_admin in application-test.yaml
NON_ADMIN_STAFF_ID = "999999"


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


def _seed_bot_and_non_admin(world: World) -> None:
    """Seed the target bot plus an ordinary (non-admin) caller."""
    _seed_bot(world)
    make_staff_user(world, user_id=NON_ADMIN_STAFF_ID)


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
        headers={"x-user-id": ADMIN_STAFF_ID},
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
        headers={"x-user-id": ADMIN_STAFF_ID},
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


def _assert_ext_untouched(response, world: World) -> None:
    """A rejected caller must not have changed the seeded bot's ext."""
    repo = world.get(BotRepository)
    _, items = repo.list_by_conditions(bot_id="test_bot", page=1, page_size=1)
    assert items, "seeded bot should still exist after a rejected PATCH"
    ext = items[0].get("ext") or {}
    assert "is_domain_bot" not in ext
    assert "arch_domain" not in ext
    assert ext.get("existing_key") == "existing_value"


@endpoint_test(
    method="PATCH",
    path="/api/public/bots/{bot_id}/ext",
    scenario="anonymous",
    input=CaseInput(
        path_params={"bot_id": "test_bot"},
        json_body={"is_domain_bot": True, "arch_domain": "越权写入"},
    ),
    seed=_seed_bot,
    expect=ExpectError(status=401),
    extra_assertions=(_assert_ext_untouched,),
)
def update_bot_ext_public_anonymous():
    """No identity → the auth dependency rejects before the handler runs."""


@endpoint_test(
    method="PATCH",
    path="/api/public/bots/{bot_id}/ext",
    scenario="non_admin_forbidden",
    input=CaseInput(
        path_params={"bot_id": "test_bot"},
        json_body={"is_domain_bot": True, "arch_domain": "越权写入"},
        headers={"x-user-id": NON_ADMIN_STAFF_ID},
    ),
    seed=_seed_bot_and_non_admin,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 403},
    ),
    extra_assertions=(_assert_ext_untouched,),
)
def update_bot_ext_public_non_admin():
    """A logged-in non-admin cannot reclassify someone else's bot."""
