"""Inventory collaboration through the real injected persistence/access services."""

import asyncio

import pytest

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import (
    BotInventoryService,
)
from agentclaw.community.core.bot_inventory.types import BusinessSpaceRef, DeployMode
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel


def _list(inventory, user="reader", **overrides):
    args = dict(
        owner_id=user,
        space=BusinessSpaceRef(
            space_id=f"personal:{user}", name="Personal", kind="personal"
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        page=1,
        page_size=20,
    )
    args.update(overrides)
    return inventory.list_items(**args)


@pytest.fixture
def stored_inventory(test_injector):
    db = test_injector.get(DatabasePlugin)
    asyncio.run(db.bootstrap())
    with db.orm_session() as session:
        spaces = []
        for kind in ["PERSONAL", "TEAM"]:
            space = SpaceModel(
                space_code=f"stored-{kind}",
                space_type=kind,
                name=kind,
                personal_owner_id="author" if kind == "PERSONAL" else None,
                env="dev",
                created_by="author",
                updated_by="author",
            )
            session.add(space)
            session.flush()
            spaces.append(space.id)
        session.add(
            SpaceMemberModel(
                space_id=spaces[1],
                user_id="reader",
                role="MEMBER",
                env="dev",
                status="ACTIVE",
                created_by="author",
            )
        )
        for i, space_id in enumerate([None, *spaces]):
            bot = BotModel(
                bot_id=f"stored-{i}",
                bot_name=f"Stored {i}",
                owner_id="author",
                creator_id="author",
                entity_id="author",
                entity_type="staff",
                bot_type="personal",
                active_engine="teclaw",
                status="ACTIVE",
                env="dev",
                space_id=space_id,
            )
            session.add(bot)
            session.flush()
            for user, role in [("reader", "member"), ("editor", "admin")]:
                session.add(
                    BotCollaboratorModel(
                        bot_pk=bot.id,
                        bot_id=bot.bot_id,
                        owner_id="author",
                        user_id=user,
                        role=role,
                        operator_id="author",
                        env="dev",
                    )
                )
        session.add(
            SpaceMemberModel(
                space_id=spaces[1],
                user_id="editor",
                role="MEMBER",
                env="dev",
                status="ACTIVE",
                created_by="author",
            )
        )
    return test_injector.get(BotInventoryService), db, spaces


@pytest.mark.parametrize(
    "user,expected", [("reader", 3), ("editor", 3), ("stranger", 0)]
)
def test_explicit_roles_and_outsider(stored_inventory, user, expected):
    inventory, _, _ = stored_inventory
    items, total = _list(inventory, user)
    assert total == len(items) == expected
    assert all(item.owner_entity_id == "author" for item in items)
    if items:
        assert len({item.card_id for item in items}) == 3
        assert items[0].template_config is None


def test_team_filter_and_revocation(stored_inventory):
    inventory, db, spaces = stored_inventory
    team = BusinessSpaceRef(space_id=str(spaces[1]), name="TEAM", kind="team")
    items, total = _list(inventory, space=team)
    assert total == 1
    assert items[0].bot_id == "stored-2"
    with db.orm_session() as session:
        session.query(SpaceMemberModel).filter_by(
            space_id=spaces[1], user_id="reader"
        ).update({"status": "INACTIVE"})
    items, total = _list(inventory)
    assert total == 2
    assert {item.bot_id for item in items} == {"stored-0", "stored-1"}


def test_filter_pagination_and_application_owner_scope(stored_inventory):
    inventory, _, _ = stored_inventory
    first, total = _list(inventory, page_size=1)
    second, total2 = _list(inventory, page=2, page_size=1)
    assert total == total2 == 3
    assert first[0].bot_id != second[0].bot_id
    filtered, count = _list(inventory, keyword="Stored 1")
    assert count == 1 and filtered[0].bot_id == "stored-1"
    assert _list(inventory, bot_ids=["stored-0"])[1] == 0
