"""Legacy MCP rows survive the existing owner-finish transaction."""

import json

import pytest
from sqlalchemy import event

from agentclaw.community.core.bot_collaborator.models import BotCollabLockModel
from agentclaw.community.core.bot_management.owner_handoff.models import (
    OwnerHandoffModel,
)
from agentclaw.community.core.models.mcp import BotMCPConfig
from agentclaw.community.core.repository.implementations.bot.owner_handoff import (
    OwnerHandoffRepository,
)
from agentclaw.community.plugin_api.models import BotModel

from ._mcp_config_support import McpWorld
from .test_mcp_config_addressing_contract import scoped_service, seed_override


def pending_handoff(world):
    with world.db.transactional_orm_session() as session:
        bot = session.get(BotModel, world.bot["id"])
        bot.ext = json.dumps({"owner_handoff": "handoff-1"})
        session.add(
            OwnerHandoffModel(
                operation_id="handoff-1",
                bot_pk=bot.id,
                bot_id=bot.bot_id,
                entity_id=bot.entity_id,
                old_owner_id="owner",
                new_owner_id="next",
                new_owner_name="Next",
                actor_id="owner",
                request_key="one",
                phase="COMMITTING_OWNER",
                execution_workno="next",
                worker="worker",
                env="dev",
            )
        )
        session.add(
            BotCollabLockModel(
                lock_key="bot-1:entity-1", holder_user_id="handoff:handoff-1", env="dev"
            )
        )
    return OwnerHandoffRepository(world.db)


@pytest.mark.parametrize("canonical", [None, {"headers": {"X-Canonical": "current"}}])
def test_forward_handoff_preserves_unvisited_legacy_config_without_giving_it_to_next_bot(
    canonical,
):
    world = McpWorld()
    legacy = {"headers": {"X-Legacy": "saved"}, "url": "https://legacy.example/mcp"}
    seed_override(world, owner_id="owner", config=legacy)
    if canonical is not None:
        seed_override(world, owner_id="entity-1", config=canonical)
    repo = pending_handoff(world)
    repo.finish("handoff-1", "worker")
    assert world.bots.get_by_id_and_owner("bot-1", "next")["entity_id"] == "entity-1"
    assert world.config.get_bot_override(
        bot_id="bot-1", owner_id="entity-1", server_code="mcp.test"
    ) == (legacy if canonical is None else canonical)
    world.bots.insert(
        {
            "bot_id": "bot-1",
            "bot_name": "Another Bot",
            "owner_id": "owner",
            "creator_id": "owner",
            "entity_id": "another-entity",
            "entity_type": "staff",
            "status": "ACTIVE",
            "active_engine": "moltis",
        }
    )
    assert (
        scoped_service(world).read(user_id="owner", server_code="mcp.test").params == ()
    )


def test_failed_handoff_rolls_back_config_normalization_owner_and_journal():
    world = McpWorld()
    legacy = {"headers": {"X-Legacy": "saved"}}
    seed_override(world, owner_id="owner", config=legacy)
    repo = pending_handoff(world)
    config_inserted = []

    def reject_owner_change(_conn, _cursor, statement, _params, _context, _many):
        sql = statement.lower().lstrip()
        if sql.startswith(("insert into ac_bot_mcp_config", "update ac_bot_mcp_config")):
            config_inserted.append(True)
        if sql.startswith("update ac_bots"):
            raise RuntimeError("Owner storage unavailable")

    event.listen(world.db.engine, "before_cursor_execute", reject_owner_change)
    try:
        with pytest.raises(RuntimeError, match="Owner storage unavailable"):
            repo.finish("handoff-1", "worker")
    finally:
        event.remove(world.db.engine, "before_cursor_execute", reject_owner_change)
    assert config_inserted
    assert world.bots.get_by_id_and_owner("bot-1", "owner")["entity_id"] == "entity-1"
    assert repo.get("handoff-1")["phase"] == "COMMITTING_OWNER"
    with world.db.orm_session() as session:
        rows = (
            session.query(BotMCPConfig)
            .filter_by(bot_id="bot-1", server_code="mcp.test")
            .all()
        )
        assert [(row.owner_id, json.loads(row.config)) for row in rows] == [
            ("owner", legacy)
        ]
        assert (
            session.query(BotCollabLockModel)
            .filter_by(lock_key="bot-1:entity-1")
            .count()
            == 1
        )
