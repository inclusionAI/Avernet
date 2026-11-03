"""Legacy personal ownership must match inventory without persisting a Bot move."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

ARGS = {"space_id": 1, "kb_id": "kb", "actor_id": "owner"}


@pytest.fixture
def personal(world):
    with world.db.orm_session() as session:
        space = session.get(SpaceModel, 1)
        space.space_type, space.personal_owner_id = "PERSONAL", "owner"
        # Same business ID, different Owners: only the personal Owner belongs here.
        session.get(BotModel, 1).space_id = None
        session.get(BotModel, 2).space_id = None
        session.get(BotModel, 4).space_id = None  # Other env.
        session.get(BotModel, 5).space_id = None  # Deleted.
        for index in range(10, 14):
            session.add(
                BotModel(
                    id=index,
                    bot_id=f"legacy-{index}",
                    bot_name=f"Legacy {index}",
                    owner_id="owner",
                    entity_id="owner",
                    entity_type="user",
                    creator_id="owner",
                    env="pre",
                    space_id=None,
                )
            )
    return world


def test_personal_candidates_page_all_five_legacy_bots_without_moving_them(personal):
    items, cursor = [], 0
    while True:
        page = personal.service.candidates(**ARGS, cursor=cursor, limit=2)
        items.extend((b["bot_id"], b["owner_id"]) for b in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert items == [("bot", "owner")] + [
        (f"legacy-{index}", "owner") for index in range(10, 14)
    ]
    assert len(personal.service.candidates(**ARGS, keyword="Legacy 12")["items"]) == 1
    with personal.db.orm_session() as session:
        assert session.get(BotModel, 1).space_id is None


def test_legacy_identity_can_read_grant_state_and_submit_grant(personal):
    state = {"items": [{"bot_id": "bot", "owner_id": "owner", "revision": 0}]}
    personal.catalog.grant_states = Mock(return_value=state)
    personal.catalog.set_grant = Mock(return_value={"granted": True, "revision": 1})
    assert personal.service.grant_states(**ARGS, identities=[("bot", "owner")]) == state
    assert personal.service.set_grant(
        **ARGS,
        bot_id="bot",
        owner_id="owner",
        granted=True,
        expected_revision=0,
    ) == {"granted": True, "revision": 1}
    personal.catalog.set_grant.assert_called_once_with(
        kb_id="kb",
        actor_id="owner",
        bot_id="bot",
        owner_id="owner",
        granted=True,
        expected_revision=0,
    )
    row = personal.service.authorized_bots(**ARGS)["items"][0]
    assert row["bot_available"] and row["bot_name"] == "Real bot"


def test_null_bot_is_not_a_team_or_another_personal_space_bot(personal):
    assert personal.source.display(2, [("bot", "owner")]) == {}
    assert personal.source.display(1, [("bot", "other-owner")]) == {}
    personal.catalog.set_grant = Mock()
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_BOT_NOT_IN_SPACE"):
        personal.service.set_grant(
            **ARGS,
            bot_id="bot",
            owner_id="other-owner",
            granted=True,
            expected_revision=0,
        )
    personal.catalog.set_grant.assert_not_called()


@pytest.mark.parametrize("change", ["deleted", "other-env", "other-owner", "team"])
def test_null_fallback_needs_live_same_env_owner_personal_space(personal, change):
    with personal.db.orm_session() as session:
        space = session.get(SpaceModel, 1)
        if change == "deleted":
            space.deleted_at = datetime.now()
        elif change == "other-env":
            space.env = "prod"
        elif change == "other-owner":
            space.personal_owner_id = "other-owner"
        else:
            space.space_type = "TEAM"
    assert personal.source.display(1, [("bot", "owner")]) == {}


def test_personal_fallback_preserves_tenant_and_manage_gates(personal):
    with avernet_tenant_scope("external"):
        with personal.db.orm_session() as session:
            session.add(
                BotModel(
                    bot_id="external",
                    owner_id="owner",
                    entity_id="owner",
                    entity_type="user",
                    creator_id="owner",
                    env="pre",
                    space_id=None,
                )
            )
    assert len(personal.service.candidates(**ARGS)["items"]) == 5
    personal.catalog.kb["can_manage"] = False
    personal.sql.clear()
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_MANAGE_REQUIRED"):
        personal.service.candidates(**ARGS)
    assert not any("ac_bots" in query for query in personal.sql)


def test_mixed_legacy_and_explicit_personal_bots_keep_cursor_order(personal):
    with personal.db.orm_session() as session:
        session.get(BotModel, 11).space_id = 1
    first = personal.service.candidates(**ARGS, limit=2)
    second = personal.service.candidates(**ARGS, limit=2, cursor=first["next_cursor"])
    third = personal.service.candidates(**ARGS, limit=2, cursor=second["next_cursor"])
    items = first["items"] + second["items"] + third["items"]
    assert [bot["bot_id"] for bot in items] == ["bot"] + [
        f"legacy-{i}" for i in range(10, 14)
    ]
    assert third["next_cursor"] is None
    assert personal.service.candidates(**ARGS, keyword="Legacy 11")["items"] == [
        next(bot for bot in items if bot["bot_id"] == "legacy-11")
    ]


def test_available_and_authorized_views_use_saved_effective_space(personal):
    from agentclaw.community.core.bot_knowledge.repository.models import (
        BotKnowledgeBindingModel,
    )
    from agentclaw.community.core.bot_knowledge.repository.store import (
        BotKnowledgeRepository,
    )
    from agentclaw.community.core.bot_knowledge.service import BotKnowledgeService
    from agentclaw.community.core.knowledge_management.repository.source import (
        ExistingKnowledgeEnablementReader,
    )
    from agentclaw.community.kernel.knowledge import BotKnowledgeScope

    BotKnowledgeBindingModel.__table__.create(personal.engine)
    personal.db.transactional_orm_session = personal.db.orm_session
    personal.catalog.list_authorized = Mock(return_value=("kb",))
    scope = BotKnowledgeScope("bot", "owner", "pre")
    selections = BotKnowledgeService(
        BotKnowledgeRepository(personal.db), personal.catalog
    )
    enabled = selections.set_enabled(
        scope=scope, actor_id="owner", kb_id="kb", enabled=True, expected_revision=0
    )
    personal.catalog.page_authorized = Mock(
        return_value={"kb_ids": ["kb"], "next_cursor": None}
    )
    personal.catalog.details = Mock(return_value=[personal.catalog.kb])
    page = personal.service.available_for_bot(scope=scope, actor_id="owner")
    assert (
        page["items"][0]["enabled"]
        and page["items"][0]["revision"] == enabled["revision"]
    )
    personal.catalog.page_authorized.assert_called_once_with(
        scope=scope, space_id=1, cursor=0, limit=20
    )
    personal.service.enablement = ExistingKnowledgeEnablementReader(personal.db, "pre")
    assert personal.service.authorized_bots(**ARGS)["items"][0]["enabled"]
    with personal.db.orm_session() as session:
        assert session.get(BotModel, 1).space_id is None


def test_registered_candidate_http_route_reads_legacy_personal_bots(client, personal):
    response = client.get(
        "/api/knowledge/spaces/1/knowledge-bases/kb/bot-candidates?limit=2"
    )
    assert response.status_code == 200
    assert [bot["bot_id"] for bot in response.json()["items"]] == ["bot", "legacy-10"]
