"""Unified Bot repository — behavior + contract.

Round-3/session-4 criteria: single ORM body, ``BotRepository``
parity. Covers the three adopt-prod behavior changes vs the old
SQLite twin: (1) env-scoping on every query/update, (2)
``update_by_owner`` honors prod's field allowlist (engine_types &
other non-allowlisted fields silently ignored), (3)
``get_device_provider_*`` performs the real ac_bots ⟕
ac_entity_device_binding join (was a None stub). Plus: plain INSERT
(no unique key), single conditional soft-delete, search_bots JOIN.
"""
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_management.repository.protocol import (
    BotLookupAmbiguousError,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.bot_repository import BotRepository
from agentclaw.community.plugins.local.sqlite_models import EntityDeviceBinding
from agentclaw.community.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bots.db'}",
        connect_args={"check_same_thread": False},
    )
    BotModel.__table__.create(engine)
    BotPublishModel.__table__.create(engine)
    EntityDeviceBinding.__table__.create(engine)
    BotCollaboratorModel.__table__.create(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(db):
    return BotRepository(db)


def _data(**ov):
    base = dict(
        bot_id="bot-1",
        bot_name="Bot One",
        bot_desc="d",
        entity_id="staff_x",
        entity_type="staff",
        creator_id="emp1",
        owner_id="emp1",
        status="ACTIVE",
        owner_name="Alice",
    )
    base.update(ov)
    return base


# ── insert (plain INSERT — table has no unique key) ─────────────────

def test_insert_and_get(repo):
    rec = repo.insert(_data())
    assert rec["id"] > 0
    assert rec["env"] == get_current_env()
    assert rec["engine_types"]  # defaulted
    got = repo.get_by_id_and_owner("bot-1", "emp1")
    assert got["bot_name"] == "Bot One"
    assert got["call_type"] == "owner"
    assert got["caller_config_revision"] == 0


def test_get_by_id_without_owner(repo):
    """Test get_by_id returns bot without requiring owner check.

    This method is used by resolve_engine_for_bot to look up
    active_engine for collaborators who don't own the bot.
    """
    rec = repo.insert(_data())
    assert rec["id"] > 0
    # Query by bot_id only (no owner check)
    got = repo.get_by_id("bot-1")
    assert got is not None
    assert got["bot_name"] == "Bot One"
    assert got["active_engine"] == "moltis"  # default from bot_repository.insert()


def test_get_by_id_returns_none_for_missing(repo):
    """Test get_by_id returns None for non-existent bot."""
    got = repo.get_by_id("non-existent-bot")
    assert got is None


def test_get_by_id_returns_none_for_deleted(repo):
    """Test get_by_id returns None for soft-deleted bots."""
    rec = repo.insert(_data())
    # Soft delete the bot
    with repo._db.orm_session() as s:
        from agentclaw.community.plugin_api.models import BotModel
        s.query(BotModel).filter(BotModel.id == rec["id"]).update(
            {BotModel.is_delete: 1}
        )
    # get_by_id should return None for deleted bots
    got = repo.get_by_id("bot-1")
    assert got is None


def test_get_by_id_and_entity_selects_the_correct_default_bot(repo):
    first = repo.insert(
        _data(bot_id="default", entity_id="entity-one", owner_id="owner-one")
    )
    second = repo.insert(
        _data(bot_id="default", entity_id="entity-two", owner_id="owner-two")
    )

    selected = repo.get_by_id_and_entity("default", "entity-two")

    assert selected is not None
    assert selected["id"] == second["id"]
    assert selected["owner_id"] == "owner-two"
    assert selected["id"] != first["id"]
    assert selected["call_type"] == "owner"
    assert selected["caller_config_revision"] == 0


def test_get_unique_by_id_rejects_duplicate_default_bots(repo):
    repo.insert(_data(bot_id="default", entity_id="entity-one"))
    repo.insert(_data(bot_id="default", entity_id="entity-two"))

    with pytest.raises(BotLookupAmbiguousError):
        repo.get_unique_by_id("default")


def test_get_unique_by_id_preserves_single_and_missing_lookups(repo):
    inserted = repo.insert(_data(bot_id="default", entity_id="entity-one"))

    selected = repo.get_unique_by_id("default")

    assert selected["id"] == inserted["id"]
    assert selected["call_type"] == "owner"
    assert selected["caller_config_revision"] == 0
    assert repo.get_unique_by_id("missing") is None


def test_insert_plain_not_upsert(repo):
    a = repo.insert(_data())
    b = repo.insert(_data())
    assert a["id"] != b["id"]


# ── env-scoping (adopt prod) ────────────────────────────────────────

def test_get_env_scoped(repo, db):
    rec = repo.insert(_data())
    with db.orm_session() as s:
        s.query(BotModel).filter(BotModel.id == rec["id"]).update(
            {BotModel.env: "other-env"}
        )
    assert repo.get_by_id_and_owner("bot-1", "emp1") is None
    assert repo.count_by_owner("emp1") == 0
    assert repo.exists_by_owner_and_bot_id("emp1", "bot-1") is False
    assert repo.exists_by_owner_and_bot_type("emp1", "personal") is False


def test_explicit_env_default_bot_read_and_ext_update_are_isolated(repo, db):
    pre = repo.insert(_data(bot_id="default", ext={"source": "pre"}))
    prod = repo.insert(_data(bot_id="default", ext={"source": "prod"}))
    with db.orm_session() as s:
        s.query(BotModel).filter(BotModel.id == pre["id"]).update(
            {BotModel.env: "pre"}
        )
        s.query(BotModel).filter(BotModel.id == prod["id"]).update(
            {BotModel.env: "prod"}
        )

    matches = repo.get_live_by_id_owner_and_env(
        bot_id="default", owner_id="emp1", env="prod"
    )
    assert [row["id"] for row in matches] == [prod["id"]]

    updated = repo.update_ext_by_id_owner_and_env(
        bot_id="default",
        owner_id="emp1",
        env="prod",
        ext={"passport": {"agent_code": "agent-prod"}},
    )
    assert updated["id"] == prod["id"]
    assert updated["ext"] == {"passport": {"agent_code": "agent-prod"}}
    assert repo.get_live_by_id_owner_and_env(
        bot_id="default", owner_id="emp1", env="pre"
    )[0]["ext"] == {"source": "pre"}


def test_explicit_env_ext_update_rolls_back_when_multiple_rows_match(repo, db):
    first = repo.insert(_data(bot_id="default", ext={"row": 1}))
    second = repo.insert(_data(bot_id="default", ext={"row": 2}))
    with db.orm_session() as s:
        s.query(BotModel).filter(BotModel.id.in_([first["id"], second["id"]])).update(
            {BotModel.env: "prod"}, synchronize_session=False
        )

    with pytest.raises(RuntimeError, match="exactly one"):
        repo.update_ext_by_id_owner_and_env(
            bot_id="default",
            owner_id="emp1",
            env="prod",
            ext={"passport": {"agent_code": "must-rollback"}},
        )

    rows = repo.get_live_by_id_owner_and_env(
        bot_id="default", owner_id="emp1", env="prod"
    )
    assert [row["ext"] for row in rows] == [{"row": 1}, {"row": 2}]


def test_list_and_count(repo):
    repo.insert(_data(bot_id="b1"))
    repo.insert(_data(bot_id="b2"))
    total, rows = repo.list_by_owner("emp1")
    assert total == 2
    assert repo.count_by_owner("emp1") == 2
    t2, _ = repo.list_by_entity(entity_id="staff_x")
    assert t2 == 2
    t3, _ = repo.list_by_conditions(bot_name="Bot")
    assert t3 == 2
    t4, _ = repo.list_by_search(search="Alice")
    assert t4 == 2


def test_list_by_conditions_owner_engine_status_filters(repo):
    """Additive owner_id / engine / status filters narrow with exact totals."""
    repo.insert(_data(bot_id="b1", owner_id="alice", active_engine="teclaw",
                      status="ACTIVE", bot_name="Alpha"))
    repo.insert(_data(bot_id="b2", owner_id="alice", active_engine="openclaw",
                      status="PENDING", bot_name="Beta"))
    repo.insert(_data(bot_id="b3", owner_id="bob", active_engine="teclaw",
                      status="ACTIVE", bot_name="Gamma"))

    # No new filters → every row (backward-compatible default).
    assert repo.list_by_conditions()[0] == 3
    # owner_id scopes to a single owner.
    total, rows = repo.list_by_conditions(owner_id="alice")
    assert total == 2
    assert {r["bot_id"] for r in rows} == {"b1", "b2"}
    # engine / status filter independently.
    assert repo.list_by_conditions(engine="teclaw")[0] == 2
    assert repo.list_by_conditions(status="PENDING")[0] == 1
    # Combined filters narrow to the exact row, with an exact total.
    total, rows = repo.list_by_conditions(
        owner_id="alice", engine="teclaw", status="ACTIVE"
    )
    assert total == 1
    assert rows[0]["bot_id"] == "b1"
    # Keyword (bot_name) still composes with the new filters.
    assert repo.list_by_conditions(owner_id="alice", bot_name="Alpha")[0] == 1


def test_count_by_owner_excludes_desktop(repo):
    repo.insert(_data(bot_id="b1", bot_type="personal"))
    repo.insert(_data(bot_id="b2", bot_type="desktop"))
    repo.insert(_data(bot_id="b3", bot_type="desktop"))
    assert repo.count_by_owner("emp1") == 3
    assert repo.count_by_owner("emp1", exclude_bot_type="desktop") == 1


def test_exists_by_owner_and_bot_type_only_matches_live_requested_type(repo):
    repo.insert(_data(bot_id="service", bot_type="service"))
    repo.insert(_data(bot_id="deleted-personal", bot_type="personal", is_delete=1))

    assert repo.exists_by_owner_and_bot_type("emp1", "personal") is False
    assert repo.exists_by_owner_and_bot_type("emp1", "service") is True

    repo.insert(_data(bot_id="live-personal", bot_type="personal"))

    assert repo.exists_by_owner_and_bot_type("emp1", "personal") is True
    assert repo.exists_by_owner_and_bot_type("other-owner", "personal") is False


# ── update_by_owner allowlist (adopt prod) ──────────────────────────

def test_update_by_owner_allowlist_drops_non_allowlisted(repo):
    repo.insert(_data(bot_id="b1", status="PENDING"))
    out = repo.update_by_owner(
        "b1",
        "emp1",
        {
            "status": "ACTIVE",          # allowlisted → applied
            "engine_types": ["x"],        # NOT allowlisted → dropped
            "creator_id": "hacker",       # NOT allowlisted → dropped
        },
    )
    assert out["status"] == "ACTIVE"
    assert out["creator_id"] == "emp1"           # unchanged
    assert out["engine_types"] != ["x"]          # unchanged (dropped)


def test_update_by_owner_json_fields(repo):
    repo.insert(_data(bot_id="b1"))
    out = repo.update_by_owner(
        "b1", "emp1", {"share_policy": {"v": 1}, "ext": {"e": 2}}
    )
    assert out["share_policy"] == {"v": 1}
    assert out["ext"] == {"e": 2}


def test_update_by_owner_missing_returns_none(repo):
    assert repo.update_by_owner("nope", "emp1", {"status": "X"}) is None


def test_update_by_owner_no_fields_returns_current(repo):
    repo.insert(_data(bot_id="b1"))
    out = repo.update_by_owner("b1", "emp1", {"not_allowed": 1})
    assert out is not None and out["bot_id"] == "b1"


# ── soft delete (single conditional is_delete=1 UPDATE) ─────────────

def test_soft_delete_is_flag_not_hard(repo, db):
    repo.insert(_data(bot_id="b1"))
    assert repo.soft_delete_by_owner("b1", "emp1") is True
    assert repo.get_by_id_and_owner("b1", "emp1") is None
    with db.orm_session() as s:
        row = s.query(BotModel).filter(
            BotModel.bot_id == "b1"
        ).first()
        assert row is not None and row.is_delete == 1  # row still there
    assert repo.soft_delete_by_owner("b1", "emp1") is False


# ── device-provider real join (adopt prod — drop stub) ──────────────

def test_get_device_provider_join(repo, db):
    repo.insert(_data(bot_id="b1", device_id="dev-9"))
    with db.orm_session() as s:
        s.add(
            EntityDeviceBinding(
                entity_id="emp1",
                entity_type="staff",
                device_id="dev-9",
                device_provider="arca",
                env="dev",
                device_props=json.dumps({"sandbox_id": "sbx-1"}),
                status="ACTIVE",
                applied_by="emp1",
            )
        )
    res = repo.get_device_provider_by_bot_id("b1")
    assert res == {"device_provider": "arca", "sandbox_id": "sbx-1", "bot_type": "personal"}
    res2 = repo.get_device_provider_by_bot_id_and_owner("b1", "emp1")
    assert res2["device_provider"] == "arca"
    assert res2["bot_type"] == "personal"
    # No bot → None
    assert repo.get_device_provider_by_bot_id("missing") is None


def test_get_device_provider_no_binding_row(repo):
    # Bot exists but no matching device binding → LEFT JOIN yields a
    # row with provider None (prod parity: dict, not None).
    repo.insert(_data(bot_id="b1", device_id="dev-x"))
    res = repo.get_device_provider_by_bot_id("b1")
    assert res == {"device_provider": None, "bot_type": "personal"}


# ── search_bots JOIN to bot_publish ─────────────────────────────────

def test_search_bots_with_publish(repo, db):
    rec = repo.insert(_data(bot_id="b1", bot_type="service"))
    with db.orm_session() as s:
        s.add(
            BotPublishModel(
                source_bot_pk=rec["id"],
                source_bot_id="b1",
                publish_bot_id="b1.pub.1",
                name="Pub",
                owner_id="emp1",
                status="RELEASE",
                last_pub_id=0,
                env=get_current_env(),
                permission_owner="emp1",
            )
        )
    total, items = repo.search_bots(bot_type="service")
    assert total == 1
    assert items[0]["publish"]["publish_bot_id"] == "b1.pub.1"
    # filter by publish status
    t2, i2 = repo.search_bots(service_status_list=["RELEASE"])
    assert t2 == 1


def test_list_active_bots_by_entity(repo):
    repo.insert(_data(bot_id="b1", status="ACTIVE", binding_id=5,
                      owner_id="emp1"))
    repo.insert(_data(bot_id="b2", status="PENDING", owner_id="emp1"))
    active = repo.list_active_bots_by_entity("emp1")
    assert [a["bot_id"] for a in active] == ["b1"]


# ── device_provider_result includes bot_type ──────────────────────

def test_device_provider_result_includes_bot_type():
    """_device_provider_result 返回的 dict 必须包含 bot_type 字段。"""
    from unittest.mock import MagicMock

    repo = BotRepository.__new__(BotRepository)
    repo.Model = MagicMock()

    # 有 bot_type 时正确透传
    result = repo._device_provider_result("baas", None, "desktop")
    assert result["bot_type"] == "desktop"

    # bot_type=None 时返回空字符串
    result_no_type = repo._device_provider_result("arca", None, None)
    assert result_no_type["bot_type"] == ""

    # bot_type="" 时也返回空字符串
    result_empty = repo._device_provider_result("baas", None, "")
    assert result_empty["bot_type"] == ""


# ── search_bots with collaborator_user_id ─────────────────────────────

def test_search_bots_owner_only(repo):
    """场景2: 仅 owner_id 查询（向后兼容）"""
    repo.insert(_data(bot_id="b1", owner_id="owner1"))
    repo.insert(_data(bot_id="b2", owner_id="owner1"))
    repo.insert(_data(bot_id="b3", owner_id="owner2"))

    total, items = repo.search_bots(owner_id="owner1")
    assert total == 2
    assert {i["bot_id"] for i in items} == {"b1", "b2"}

    # 不含 user_role 字段
    assert "user_role" not in items[0]


def test_search_bots_collaborator_only(repo, db):
    """场景3: 仅 collaborator_user_id 查询"""
    # 创建 bot
    rec1 = repo.insert(_data(bot_id="b1", owner_id="owner1"))
    rec2 = repo.insert(_data(bot_id="b2", owner_id="owner1"))
    rec3 = repo.insert(_data(bot_id="b3", owner_id="owner2"))

    # 添加协作者关系：user1 是 b1 的 admin，b2 的 member
    env = get_current_env()
    with db.orm_session() as s:
        s.add(BotCollaboratorModel(
            bot_pk=rec1["id"],
            bot_id="b1",
            owner_id="owner1",
            user_id="user1",
            user_name="User One",
            role="admin",
            operator_id="owner1",
            env=env,
        ))
        s.add(BotCollaboratorModel(
            bot_pk=rec2["id"],
            bot_id="b2",
            owner_id="owner1",
            user_id="user1",
            user_name="User One",
            role="member",
            operator_id="owner1",
            env=env,
        ))

    # 查询 user1 作为协作者的 bot
    total, items = repo.search_bots(collaborator_user_id="user1")
    assert total == 2
    bot_ids = {i["bot_id"] for i in items}
    assert bot_ids == {"b1", "b2"}

    # 验证 user_role 正确返回
    items_dict = {i["bot_id"]: i for i in items}
    assert items_dict["b1"]["user_role"] == "admin"
    assert items_dict["b2"]["user_role"] == "member"


def test_search_bots_owner_and_collaborator(repo, db):
    """场景1: owner_id + collaborator_user_id 组合查询（OR 关系）"""
    # 创建 bot
    rec1 = repo.insert(_data(bot_id="b1", owner_id="user1"))  # user1 是 owner
    rec2 = repo.insert(_data(bot_id="b2", owner_id="owner2"))  # user1 是协作者
    rec3 = repo.insert(_data(bot_id="b3", owner_id="owner3"))  # user1 无关系

    # user1 是 b2 的协作者
    env = get_current_env()
    with db.orm_session() as s:
        s.add(BotCollaboratorModel(
            bot_pk=rec2["id"],
            bot_id="b2",
            owner_id="owner2",
            user_id="user1",
            user_name="User One",
            role="admin",
            operator_id="owner2",
            env=env,
        ))

    # 查询 user1 是 owner 或协作者的 bot
    total, items = repo.search_bots(
        owner_id="user1",
        collaborator_user_id="user1"
    )
    assert total == 2
    bot_ids = {i["bot_id"] for i in items}
    assert bot_ids == {"b1", "b2"}

    # b1 是 owner，user_role 为 None
    # b2 是协作者，user_role 为 admin
    items_dict = {i["bot_id"]: i for i in items}
    assert items_dict["b1"]["user_role"] is None
    assert items_dict["b2"]["user_role"] == "admin"


def test_search_bots_user_role_none_for_owner(repo, db):
    """验证 owner 的 user_role 为 None"""
    rec = repo.insert(_data(bot_id="b1", owner_id="owner1"))

    # owner 同时也是协作者（异常情况，但应正确处理）
    env = get_current_env()
    with db.orm_session() as s:
        s.add(BotCollaboratorModel(
            bot_pk=rec["id"],
            bot_id="b1",
            owner_id="owner1",
            user_id="owner1",
            user_name="Owner",
            role="admin",
            operator_id="owner1",
            env=env,
        ))

    total, items = repo.search_bots(collaborator_user_id="owner1")
    assert total == 1
    # LEFT JOIN 会返回协作者的 role
    assert items[0]["user_role"] == "admin"


def test_search_bots_key_only_bot_name(repo, db):
    """验证 key 仅搜索 bot_name，不再搜索 owner_name"""
    repo.insert(_data(bot_id="b1", bot_name="TestBot", owner_name="Alice"))
    repo.insert(_data(bot_id="b2", bot_name="OtherBot", owner_name="TestUser"))

    # 搜索 "Test" 仅匹配 bot_name 包含 "Test" 的
    # b1 的 bot_name="TestBot" 匹配
    # b2 的 bot_name="OtherBot" 不匹配（即使 owner_name="TestUser"）
    total, items = repo.search_bots(key="Test")
    assert total == 1
    assert items[0]["bot_id"] == "b1"


def test_search_bots_no_collaborator_match(repo, db):
    """查询不存在协作者关系的用户，返回空"""
    repo.insert(_data(bot_id="b1", owner_id="owner1"))

    total, items = repo.search_bots(collaborator_user_id="unknown_user")
    assert total == 0
    assert items == []
# ── search_bots filters ────────────────────────────────────────────

def test_search_bots_filter_bot_id_and_template_type(repo):
    repo.insert(_data(bot_id="s1", template_type="applicationCoding"))
    repo.insert(_data(bot_id="s2", template_type="personalCoding"))
    t1, i1 = repo.search_bots(bot_id="s1", page=1, page_size=10)
    assert t1 == 1
    assert [b["bot_id"] for b in i1] == ["s1"]
    t2, i2 = repo.search_bots(template_type="personalCoding", page=1, page_size=10)
    assert t2 == 1
    assert [b["bot_id"] for b in i2] == ["s2"]


def test_search_bots_filter_provider(repo, db):
    repo.insert(_data(bot_id="pa", device_id="dev-arca"))
    repo.insert(_data(bot_id="pb", device_id="dev-baas"))
    with db.orm_session() as s:
        s.add(
            EntityDeviceBinding(
                entity_id="emp1",
                entity_type="staff",
                device_id="dev-arca",
                device_provider="arca",
                env="dev",
                device_props="{}",
                status="ACTIVE",
                applied_by="emp1",
            )
        )
        s.add(
            EntityDeviceBinding(
                entity_id="emp1",
                entity_type="staff",
                device_id="dev-baas",
                device_provider="baas",
                env="dev",
                device_props="{}",
                status="ACTIVE",
                applied_by="emp1",
            )
        )
    total, items = repo.search_bots(provider="arca", page=1, page_size=10)
    assert total == 1
    assert [b["bot_id"] for b in items] == ["pa"]


def test_search_bots_filter_provider_cross_env_binding_no_match(repo, db):
    repo.insert(_data(bot_id="pc", device_id="dev-cross"))
    with db.orm_session() as s:
        s.add(
            EntityDeviceBinding(
                entity_id="emp1",
                entity_type="staff",
                device_id="dev-cross",
                device_provider="arca",
                env="pre",
                device_props="{}",
                status="ACTIVE",
                applied_by="emp1",
            )
        )
    total, items = repo.search_bots(provider="arca", page=1, page_size=10)
    assert total == 0
    assert items == []


def test_list_by_conditions_treats_like_wildcards_literally(repo):
    """R9/F39: a `%` in the keyword narrowed nothing — it matched everything.

    The keyword goes into a LIKE pattern, so an unescaped `%` or `_` is a
    wildcard rather than the character the caller typed: searching for a name
    containing `%` returned every bot and an inflated total.
    """
    repo.insert(_data(bot_id="b1", bot_name="100% Bot"))
    repo.insert(_data(bot_id="b2", bot_name="Plain Bot"))
    repo.insert(_data(bot_id="b3", bot_name="a_b Bot"))
    repo.insert(_data(bot_id="b4", bot_name="axb Bot"))

    # `%` is the literal character, not "match anything".
    total, rows = repo.list_by_conditions(bot_name="100%")
    assert total == 1, [r["bot_name"] for r in rows]
    assert rows[0]["bot_id"] == "b1"

    # `_` is the literal character, not "match one".
    total, rows = repo.list_by_conditions(bot_name="a_b")
    assert total == 1, [r["bot_name"] for r in rows]
    assert rows[0]["bot_id"] == "b3"

    # A bare wildcard matches only names actually containing it.
    assert repo.list_by_conditions(bot_name="%")[0] == 1

    # Ordinary substring search is unchanged.
    assert repo.list_by_conditions(bot_name="Bot")[0] == 4
