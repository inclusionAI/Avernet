"""An explicit human-owner lookup cannot borrow an entity alias."""

from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.core.apply_engine._mcp_config_support import McpWorld


def _add(world, entity_id, *, owner_id="owner", **extra):
    return world.bots.insert({
        "bot_id": "bot-1", "bot_name": "Owner locator", "owner_id": owner_id, "creator_id": owner_id,
        "entity_id": entity_id, "entity_type": "staff", "status": "ACTIVE", "active_engine": "moltis", **extra,
    })


def test_current_owner_lookup_is_explicit_live_tenant_env_scoped_and_bounded(monkeypatch):
    world = McpWorld()
    _add(world, "owner", owner_id="another-owner")
    _add(world, "deleted", is_delete=1)
    with avernet_tenant_scope("another-tenant"):
        _add(world, "another-tenant-entity")
    with monkeypatch.context() as patch:
        patch.setenv("SERVER_ENV", "pre")
        _add(world, "another-env-entity")
    matches = world.bots.list_matches_by_current_owner("bot-1", "owner")
    assert [bot["id"] for bot in matches] == [world.bot["id"]]
    _add(world, "second")
    _add(world, "third")
    matches = world.bots.list_matches_by_current_owner("bot-1", "owner")
    assert len(matches) == 2
    assert all(bot["owner_id"] == "owner" and bot["entity_id"] != "owner" for bot in matches)
