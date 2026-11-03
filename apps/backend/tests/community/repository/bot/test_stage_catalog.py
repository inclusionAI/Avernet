"""Stage filtering shares runtime selection and precedes mixed-Bot pagination."""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env


@pytest.fixture
def catalog():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (BotModel, BotPublishModel, EntityDeviceBinding):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine)

    class Database:
        @contextmanager
        def orm_session(self):
            with factory.begin() as session:
                yield session

    database = Database()
    yield BotRepository(database), database
    engine.dispose()


def bot(repo, name, *, kind="service", owner="owner", binding=10):
    return repo.insert(dict(
        bot_id=name, bot_name=name, owner_id=owner, creator_id=owner,
        entity_id=owner, entity_type="staff", bot_type=kind,
        binding_id=binding, status="ACTIVE",
    ))


def publish(db, source, *, status="success", bindings=None, version=1, env=None):
    with db.orm_session() as session:
        session.add(BotPublishModel(
            source_bot_pk=source["id"], source_bot_id=source["bot_id"],
            publish_bot_id=source["bot_id"], name=source["bot_id"],
            owner_id=source["owner_id"], permission_owner=source["owner_id"],
            env=env or get_current_env(), status=status, version=version,
            ext=json.dumps({"binding": bindings or {}}),
            gmt_create=datetime(2026, 10, 1) + timedelta(days=version),
        ))


@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
def test_personal_bots_are_unchanged_by_stage(catalog, stage):
    repo, _ = catalog
    personal = bot(repo, "personal", kind="personal", binding=None)
    total, rows = repo.list_by_conditions(owner_id="owner", stage=stage)
    assert total == 1
    assert rows[0]["id"] == personal["id"]


def test_online_uses_success_not_latest_draft_and_filters_before_paging(catalog):
    repo, db = catalog
    bot(repo, "personal", kind="personal")
    online = bot(repo, "online")
    publish(db, online, bindings={"online": 71})
    publish(db, online, status="draft", version=2)
    bot(repo, "draft-only")
    failed = bot(repo, "failed")
    publish(db, failed, status="failed", bindings={"online": 72})
    unbound = bot(repo, "unbound")
    publish(db, unbound)
    foreign = bot(repo, "wrong-env")
    publish(db, foreign, bindings={"online": 73}, env="other")
    deleted = bot(repo, "deleted")
    publish(db, deleted, bindings={"online": 74})
    with db.orm_session() as session:
        session.get(BotModel, deleted["id"]).is_delete = 1
    names = []
    for page in (1, 2, 3):
        total, rows = repo.list_by_conditions(owner_id="owner", stage="online", page=page, page_size=1)
        assert total == 2
        names.extend(row["bot_id"] for row in rows)
    assert set(names) == {"personal", "online"}
    assert repo.list_by_conditions(owner_id="owner")[0] == 6


def test_newest_success_without_binding_does_not_fall_back(catalog):
    repo, db = catalog
    source = bot(repo, "online")
    publish(db, source, bindings={"online": 71})
    publish(db, source, version=2)
    assert repo.list_by_conditions(owner_id="owner", stage="online")[0] == 0


def test_collaboration_scope_and_pagination_are_preserved(catalog):
    repo, db = catalog
    personal = bot(repo, "personal", kind="personal", owner="other")
    online = bot(repo, "online", owner="other")
    draft = bot(repo, "draft-only", owner="other")
    mine = bot(repo, "mine")
    publish(db, online, bindings={"online": 71})
    publish(db, mine, bindings={"online": 72})
    total, rows = repo.page_live_bots_by_pks(
        [item["id"] for item in (personal, online, draft, mine)],
        exclude_owner_id="owner", entity_pairs=None, page=2, page_size=1, stage="online",
    )
    assert total == 2
    assert len(rows) == 1
    assert rows[0]["owner_id"] == "other"
    assert repo.page_live_bots_by_pks(
        [online["id"]], exclude_owner_id="owner", entity_pairs=[],
        page=1, page_size=20, stage="online",
    ) == (0, [])


def test_metadata_retains_drafts_and_exposes_only_safe_stage_projection(catalog):
    repo, db = catalog
    online = bot(repo, "online")
    publish(db, online, bindings={"online": 71}, version=3)
    draft = bot(repo, "draft-only")
    personal = bot(repo, "personal", kind="personal")
    total, rows = repo.list_bots_by_owner_bot_pairs([
        (item["bot_id"], item["owner_id"]) for item in (online, draft, personal)
    ])
    assert total == 3
    by_id = {row["bot_id"]: row for row in rows}
    assert by_id["online"]["online_available"] is True
    assert by_id["online"]["live_version"] == 3
    assert by_id["draft-only"]["online_available"] is False
    assert by_id["draft-only"]["live_version"] is None
    assert by_id["personal"]["online_available"] is None
    assert by_id["personal"]["conversation_stage"] == "draft"
    assert by_id["online"]["conversation_stage"] == "online"


def test_same_bot_id_different_owner_does_not_share_publication(catalog):
    repo, db = catalog
    published = bot(repo, "default", owner="a")
    bot(repo, "default", owner="b")
    publish(db, published, bindings={"online": 71})
    assert repo.list_by_conditions(owner_id="a", stage="online")[0] == 1
    assert repo.list_by_conditions(owner_id="b", stage="online")[0] == 0


@pytest.mark.parametrize("binding_status,expected", [("ACTIVE", 1), ("RELEASED", 0)])
def test_retained_verify_requires_active_binding(catalog, binding_status, expected):
    repo, db = catalog
    source = bot(repo, "retained-verify")
    publish(db, source, bindings={"online": 71, "verify": 72})
    with db.orm_session() as session:
        session.add(EntityDeviceBinding(
            id=72, entity_id="owner", entity_type="staff", device_id="verify-device",
            device_provider="arca", env=get_current_env(), status=binding_status,
            applied_by="owner",
        ))
    assert repo.list_by_conditions(owner_id="owner", stage="verify")[0] == expected


def test_unbound_validating_record_does_not_use_retained_verify(catalog):
    repo, db = catalog
    source = bot(repo, "verify")
    publish(db, source, bindings={"online": 71, "verify": 72})
    publish(db, source, status="validating", version=2)
    assert repo.list_by_conditions(owner_id="owner", stage="verify")[0] == 0


def test_negative_online_binding_is_not_a_published_target(catalog):
    repo, db = catalog
    source = bot(repo, "corrupt-binding")
    publish(db, source, bindings={"online": -1})
    assert repo.list_by_conditions(owner_id="owner", stage="online")[0] == 0
