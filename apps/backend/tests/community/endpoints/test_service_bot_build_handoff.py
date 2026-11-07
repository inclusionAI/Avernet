"""Build settings keep entity locators while authority follows the current Owner."""

from agentclaw.community.core.repository.protocols.bot import BotRepository
from tests.community.factories.access import make_staff_user
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


def _seed_transferred_bot(world):
    make_staff_user(world, user_id="old-owner")
    make_staff_user(world, user_id="new-owner")
    world.get(BotRepository).insert({
        "bot_id": "build-handoff", "bot_name": "Transferred build settings",
        "entity_id": "staff_old-owner", "entity_type": "staff",
        "owner_id": "new-owner", "creator_id": "old-owner",
        "bot_type": "service", "active_engine": "openclaw", "status": "ACTIVE",
        "ext": {"build_rsync_excludes": ["handoff-cache/"]},
    })


@endpoint_test(
    method="GET", path="/api/service-bot/rsync-excludes",
    scenario="handoff_new_owner_with_entity_locator",
    input=CaseInput(headers={"x-user-id": "new-owner"}, query_params={
        "bot_id": "build-handoff", "owner_id": "staff_old-owner",
    }),
    seed=_seed_transferred_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {
        "bot_id": "build-handoff", "engine_type": "openclaw",
        "custom_excludes": ["handoff-cache/"],
    }}),
)
def test_rsync_excludes_after_handoff():
    """Stable locator still reaches the transferred Bot's build configuration."""


@endpoint_test(
    method="GET", path="/api/service-bot/read-only/tree",
    scenario="handoff_new_owner_with_entity_locator",
    input=CaseInput(headers={"x-user-id": "new-owner"}, query_params={
        "bot_id": "build-handoff", "owner_id": "staff_old-owner",
    }),
    seed=_seed_transferred_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def test_read_only_tree_after_handoff():
    """The new Owner can read tree rules through the unchanged entity locator."""


@endpoint_test(
    method="GET", path="/api/service-bot/rsync-excludes",
    scenario="handoff_old_owner_denied",
    input=CaseInput(headers={"x-user-id": "old-owner"}, query_params={
        "bot_id": "build-handoff", "owner_id": "staff_old-owner",
    }),
    seed=_seed_transferred_bot,
    expect=ExpectError(status=403, json_contains={"success": False, "error_code": 403}),
)
def test_old_owner_cannot_read_rsync_excludes_after_handoff():
    """Knowing the resource locator does not preserve Owner permission."""


@endpoint_test(
    method="GET", path="/api/service-bot/read-only/tree",
    scenario="handoff_old_owner_denied",
    input=CaseInput(headers={"x-user-id": "old-owner"}, query_params={
        "bot_id": "build-handoff", "owner_id": "staff_old-owner",
    }),
    seed=_seed_transferred_bot,
    expect=ExpectError(status=403, json_contains={"success": False, "error_code": 403}),
)
def test_old_owner_cannot_read_tree_after_handoff():
    """Tree access authorizes the current Owner instead of the locator's creator."""
