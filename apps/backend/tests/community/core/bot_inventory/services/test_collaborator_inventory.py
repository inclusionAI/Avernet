"""Inventory reachability against the real ORM and injected services."""

from __future__ import annotations

import asyncio

import pytest

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import (
    BotInventoryService,
)
from agentclaw.community.core.bot_inventory.types import BusinessSpaceRef, DeployMode
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel


@pytest.fixture
def inventory_world(test_injector):
    db = test_injector.get(DatabasePlugin)
    asyncio.run(db.bootstrap())
    with db.orm_session() as session:
        bot = BotModel(
            bot_id="shared-service",
            bot_name="Shared Service",
            owner_id="owner",
            creator_id="owner",
            entity_id="owner",
            entity_type="staff",
            bot_type="service",
            active_engine="teclaw",
            status="ACTIVE",
            env="dev",
        )
        session.add(bot)
        session.flush()
        session.add(
            BotCollaboratorModel(
                bot_pk=bot.id,
                bot_id=bot.bot_id,
                owner_id="owner",
                user_id="editor",
                role="admin",
                operator_id="owner",
                env="dev",
            )
        )
    return test_injector.get(BotInventoryService), db


@pytest.mark.parametrize(
    "user,expected", [("owner", 1), ("editor", 1), ("outsider", 0)]
)
def test_personal_inventory_includes_explicit_collaborator(
    inventory_world, user, expected
):
    inventory, _ = inventory_world
    items, total = inventory.list_items(
        owner_id=user,
        space=BusinessSpaceRef(
            space_id=f"personal:{user}", name="Personal", kind="personal"
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        is_service=True,
        page=1,
        page_size=20,
    )
    assert total == expected
    assert len(items) == expected
    if items:
        assert items[0].owner_entity_id == "owner"
        assert items[0].space.space_id == "personal:owner"


def _list(inventory, **overrides):
    args = dict(
        owner_id="editor",
        space=BusinessSpaceRef(
            space_id="personal:editor", name="Personal", kind="personal"
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        is_service=True,
        page=1,
        page_size=20,
    )
    args.update(overrides)
    return inventory.list_items(**args)


def _assign_space(db, kind, *, active_member=True):
    from agentclaw.community.core.spaces.repository.models import (
        SpaceModel,
        SpaceMemberModel,
    )

    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=901,
                space_code="source-space",
                space_type=kind,
                name="Source",
                personal_owner_id="owner" if kind == "PERSONAL" else None,
                env="dev",
                created_by="owner",
                updated_by="owner",
            )
        )
        if kind == "TEAM":
            session.add(
                SpaceMemberModel(
                    space_id=901,
                    user_id="editor",
                    role="MEMBER",
                    status="ACTIVE" if active_member else "INACTIVE",
                    env="dev",
                    created_by="owner",
                )
            )
        session.query(BotModel).filter_by(bot_id="shared-service").update(
            {"space_id": 901}
        )


@pytest.mark.parametrize("kind", ["PERSONAL", "TEAM"])
@pytest.mark.parametrize("role", ["admin", "member"])
def test_cross_space_collaborators_preserve_source_identity(
    inventory_world, kind, role
):
    inventory, db = inventory_world
    _assign_space(db, kind)
    with db.orm_session() as session:
        session.query(BotCollaboratorModel).update({"role": role})
    items, total = _list(inventory)
    assert total == 1
    assert items[0].owner_entity_id == "owner"
    assert items[0].space.space_id == "901"


def test_stale_team_membership_is_not_visibility(inventory_world):
    inventory, db = inventory_world
    _assign_space(db, "TEAM", active_member=False)
    assert _list(inventory) == ([], 0)


@pytest.mark.parametrize(
    "change", ["revoked", "deleted", "other-env", "wrong-pk", "wrong-owner"]
)
def test_invalid_collaboration_cannot_add_cards(inventory_world, change):
    inventory, db = inventory_world
    with db.orm_session() as session:
        if change == "revoked":
            session.query(BotCollaboratorModel).delete()
        elif change == "deleted":
            session.query(BotModel).update({"is_delete": 1})
        elif change == "other-env":
            session.query(BotCollaboratorModel).update({"env": "prod"})
        elif change == "wrong-pk":
            session.query(BotCollaboratorModel).update({"bot_pk": 9001})
        else:
            session.query(BotCollaboratorModel).update({"owner_id": "wrong-owner"})
    assert _list(inventory) == ([], 0)


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"keyword": "Shared"}, 1),
        ({"keyword": "absent"}, 0),
        ({"engine": "teclaw"}, 1),
        ({"engine": "openclaw"}, 0),
        ({"is_service": False}, 0),
        ({"is_service": None}, 1),
        ({"deploy_mode": DeployMode.LOCAL}, 0),
        ({"page": 2, "page_size": 1}, 1),
        ({"bot_ids": []}, 0),
        ({"bot_ids": ["shared-service"]}, 0),
    ],
)
def test_shared_inventory_filters_and_application_scope(
    inventory_world, filters, expected
):
    inventory, _ = inventory_world
    items, total = _list(inventory, **filters)
    assert total == expected
    assert len(items) == (0 if filters.get("page") == 2 else expected)


def test_selected_team_does_not_include_other_space_collaborations(inventory_world):
    inventory, _ = inventory_world
    items, total = _list(
        inventory, space=BusinessSpaceRef(space_id="902", name="Selected", kind="team")
    )
    assert (items, total) == ([], 0)


def test_shared_card_never_loads_owner_template_credentials(inventory_world):
    from unittest.mock import MagicMock

    inventory, db = inventory_world
    with db.orm_session() as session:
        session.query(BotModel).update({"template_type": "applicationCoding"})
    templates = MagicMock()
    templates.list_template_configs_by_bot_ids.return_value = {
        "shared-service": {"token": "owner-secret"}
    }
    inventory._template_port = templates
    items, total = _list(inventory)
    assert total == 1
    assert items[0].template_config is None
    templates.list_template_configs_by_bot_ids.assert_not_called()


def test_other_tenant_collaboration_cannot_add_cards(inventory_world):
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    inventory, _ = inventory_world
    with avernet_tenant_scope("another-tenant"):
        assert _list(inventory) == ([], 0)


def test_publications_expand_before_shared_card_pagination(inventory_world):
    from agentclaw.community.core.service_bot.repository.models import BotPublishModel

    inventory, db = inventory_world
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="shared-service").one()
        for version, status in [(1, "success"), (2, "draft")]:
            session.add(
                BotPublishModel(
                    source_bot_pk=bot.id,
                    source_bot_id=bot.bot_id,
                    publish_bot_id=bot.bot_id,
                    name="Shared",
                    owner_id="owner",
                    status=status,
                    version=version,
                    env="dev",
                    permission_owner="owner",
                )
            )
    first, total = _list(inventory, page_size=1)
    second, second_total = _list(inventory, page=2, page_size=1)
    assert total == second_total == 2
    assert first[0].card_id != second[0].card_id
    assert {first[0].publication_version, second[0].publication_version} == {1, 2}


@pytest.mark.parametrize("engine", [None, "aicoding"])
def test_same_bot_id_across_owners_keeps_publications_separate(inventory_world, engine):
    from agentclaw.community.core.service_bot.repository.models import BotPublishModel

    inventory, db = inventory_world
    with db.orm_session() as session:
        shared = session.query(BotModel).filter_by(owner_id="owner").one()
        shared.active_engine = "aicoding"
        own = BotModel(
            bot_id=shared.bot_id,
            bot_name="Owned",
            owner_id="editor",
            creator_id="editor",
            entity_id="editor",
            entity_type="staff",
            bot_type="service",
            active_engine="aicoding",
            status="ACTIVE",
            env="dev",
        )
        session.add(own)
        session.flush()
        for row, version in [(shared, 1), (own, 2)]:
            session.add(
                BotPublishModel(
                    source_bot_pk=row.id,
                    source_bot_id=row.bot_id,
                    publish_bot_id=row.bot_id,
                    name=row.bot_name,
                    owner_id=row.owner_id,
                    status="success",
                    version=version,
                    env="dev",
                    permission_owner=row.owner_id,
                )
            )
    items, total = _list(inventory, engine=engine)
    assert total == 2
    assert {item.owner_entity_id: item.publication_version for item in items} == {
        "owner": 1,
        "editor": 2,
    }
    assert len({item.card_id for item in items}) == 2


def test_unpublished_same_id_cards_have_distinct_stable_keys(inventory_world):
    inventory, db = inventory_world
    with db.orm_session() as session:
        session.add(
            BotModel(
                bot_id="shared-service",
                bot_name="Owned",
                owner_id="editor",
                creator_id="editor",
                entity_id="editor",
                entity_type="staff",
                bot_type="service",
                active_engine="teclaw",
                status="ACTIVE",
                env="dev",
            )
        )
    items, total = _list(inventory)
    assert total == 2
    assert len({item.card_id for item in items}) == 2
    shared_key = next(item.card_id for item in items if item.owner_entity_id == "owner")
    filtered, _ = _list(inventory, keyword="Shared")
    assert filtered[0].card_id == shared_key
