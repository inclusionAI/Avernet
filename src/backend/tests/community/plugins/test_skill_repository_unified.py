"""Unified Skill / SkillSet repositories — behavior + contract.

Round-3/session-4 criteria: single ORM body per Protocol, faithful
prod port. Covers: plain INSERT create/add_* (no upsert), hard
delete, single UPDATEs, the cross-table cascade
(delete_by_name_with_cascade across ac_skill_set_skill +
ac_skill_member + ac_skill), set_active_skill_set 2-step
clear+activate, get_skills_in_set center/non-center + MAX(version),
the user_id anonymous<->0 coercion, and the ONLY S4 dialect-aware
upsert (add_default_mcp_exclusion idempotent on
uk_user_bot_skillset_mcp).
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.models import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import AcSkillMember
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.local.sqlite_models import (
    DefaultSkillsetMcpExclusion,
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.plugins.skill_repository import (
    SkillRepository,
    SkillSetRepository,
)

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
        f"sqlite:///{tmp_path / 'skill.db'}",
        connect_args={"check_same_thread": False},
    )
    for m in (
        Skill,
        SkillSet,
        SkillSetSkill,
        SkillSetMCPServer,
        DefaultSkillsetMcpExclusion,
        DefaultSkillsetSkillExclusion,
        BotModel,
        AcSkillMember,
    ):
        m.__table__.create(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def skills(db):
    return SkillRepository(db)


@pytest.fixture
def sets(db):
    return SkillSetRepository(db)


# ── SkillRepository ─────────────────────────────────────────────────

def test_skill_create_get_update_delete(skills):
    rec = skills.create(
        {"name": "S1", "user_id": "123", "git_path": "git://a/b",
         "skill_uuid": "u1", "status": "PUBLISHED"}
    )
    assert rec["id"]
    assert rec["user_id"] == "123"  # _format_user_id
    assert rec["link_name"] == "a_b"  # derived from git://
    sid = rec["id"]
    assert skills.get_by_id(sid)["name"] == "S1"
    assert skills.get_by_uuid("u1")["id"] == sid

    upd = skills.update(sid, {"name": "S1b", "is_public": False})
    assert upd["name"] == "S1b"

    assert skills.delete(sid) is True
    assert skills.get_by_id(sid) is None  # hard delete
    assert skills.delete(sid) is False


def test_skill_create_is_plain_insert_not_upsert(skills):
    # Distinct versions → two independent rows (plain INSERT, not an
    # upsert that would update-in-place).
    a = skills.create({"name": "dup", "skill_uuid": "x", "version": 1})
    b = skills.create({"name": "dup", "skill_uuid": "x", "version": 2})
    assert a["id"] != b["id"]
    assert len(skills.list_skills()) == 2


def test_skill_create_hits_sqlite_only_unique_constraint(skills):
    """DOCUMENTED pre-existing divergence: the SQLite ``Skill`` model
    declares UniqueConstraint(skill_uuid,name,env,version) that prod
    ``ac_skill`` DDL does NOT have. ``create`` is a plain INSERT (prod
    parity) — so a duplicate (skill_uuid,name,env,version) raises
    IntegrityError on SQLite, whereas prod OceanBase would accept it.
    Asserting the plain-INSERT shape here (NOT an upsert that would
    silently update). Flagged in the DDL-parity doc + Pre check."""
    import pytest as _pytest
    from sqlalchemy.exc import IntegrityError

    skills.create({"name": "d", "skill_uuid": "x", "version": 1})
    with _pytest.raises(IntegrityError):
        skills.create({"name": "d", "skill_uuid": "x", "version": 1})


def test_skill_user_id_anonymous_coercion(skills):
    rec = skills.create({"name": "anon", "user_id": "anonymous"})
    assert rec["user_id"] == "anonymous"  # 0 -> "anonymous"
    got = skills.get_by_name_global_include_deleted(
        "anon", user_id="anonymous"
    )
    assert got is not None


def test_skill_list_and_published_center(skills):
    skills.create({"name": "g", "git_path": "git://x", "bolt_id": "default"})
    skills.create(
        {"name": "c", "git_path": "center://y", "status": "PUBLISHED",
         "skill_uuid": "cu"}
    )
    lst = skills.list_skills(bolt_id="default")
    assert {s["name"] for s in lst} == {"g", "c"}
    centers = skills.list_published_center_skills()
    assert [c["name"] for c in centers] == ["c"]


def test_get_bot_local_by_name_never_falls_back_to_global(skills):
    skills.create(
        {
            "name": "same",
            "git_path": "local:///global/same",
            "bolt_id": None,
            "user_id": "owner",
        }
    )
    bot_owned = skills.create(
        {
            "name": "same",
            "git_path": "local:///bot/same",
            "bolt_id": "bot-x",
            "user_id": "owner",
        }
    )

    assert skills.get_bot_local_by_name(
        bot_id="bot-x",
        name="same",
        user_id="owner",
    )["id"] == bot_owned["id"]
    assert (
        skills.get_bot_local_by_name(
            bot_id="bot-y",
            name="same",
            user_id="owner",
        )
        is None
    )


def test_skill_update_risk_and_mcp(skills):
    sid = skills.create({"name": "r"})["id"]
    assert skills.update_risk_tags(sid, ["high"])["risk_tags"] == [
        "high"
    ]
    assert skills.update_mcp_dependencies(sid, ["m1"])[
        "mcp_dependencies"
    ] == ["m1"]


def test_delete_by_name_with_cascade(skills, db):
    skills.create(
        {"name": "casc", "skill_uuid": "cu1", "status": "PUBLISHED"}
    )
    with db.orm_session() as s:
        s.add(SkillSetSkill(skill_set_id=1, skill_id=999,
                            skill_uuid="cu1"))
        s.add(AcSkillMember(skill_uuid="cu1", user_id="u",
                            role="member"))
    res = skills.delete_by_name_with_cascade("casc")
    assert res["deleted_skill_count"] == 1
    assert res["cleaned_set_skill"] == 1
    assert res["cleaned_member"] == 1
    with db.orm_session() as s:
        assert s.query(SkillSetSkill).count() == 0
        assert s.query(AcSkillMember).count() == 0
        assert s.query(Skill).count() == 0


def test_delete_by_bot_id(skills):
    skills.create({"name": "b1", "bolt_id": "bot-x"})
    skills.create({"name": "b2", "bolt_id": "bot-x"})
    assert skills.delete_by_bot_id("bot-x") == 2
    assert skills.list_skills(bolt_id="bot-x") == []


def test_skills_pool_asset_views_are_exactly_bot_scoped(skills, sets):
    local = skills.create(
        {
            "name": "local-a",
            "git_path": "local:///legacy/local-a",
            "bolt_id": "bot-x",
        }
    )
    repo = skills.create(
        {
            "name": "repo-a",
            "git_path": "git://business/repo-a",
            "bolt_id": "bot-x",
        }
    )
    skills.create(
        {
            "name": "other-local",
            "git_path": "local:///legacy/other-local",
            "bolt_id": "bot-y",
        }
    )
    skill_set = sets.create(
        {
            "name": "active",
            "bolt_id": "bot-x",
            "user_id": "owner-x",
            "engine_type": "openclaw",
            "is_active": True,
        }
    )
    sets.add_skill_to_set(skill_set["id"], local["id"])
    sets.add_skill_to_set(skill_set["id"], repo["id"])

    local_assets = skills.list_bot_local_assets(
        env=local["env"],
        bot_id="bot-x",
    )
    active_assets = skills.list_bot_active_assets(
        env=local["env"],
        bot_id="bot-x",
        user_id="owner-x",
        engine="openclaw",
    )

    assert [(asset.skill_id, asset.name) for asset in local_assets] == [
        (int(local["id"]), "local-a")
    ]
    assert {asset.git_path for asset in active_assets} == {
        "local:///legacy/local-a",
        "git://business/repo-a",
    }


def test_skills_pool_active_assets_include_default_set_and_exclusions(
    skills, sets
):
    default_enabled = skills.create(
        {
            "name": "default-enabled",
            "git_path": "git://defaults/enabled",
        }
    )
    default_excluded = skills.create(
        {
            "name": "default-excluded",
            "git_path": "git://defaults/excluded",
        }
    )
    default_set = sets.create(
        {
            "name": "OpenClaw defaults",
            "is_default": True,
            "is_active": True,
            "engine_type": "openclaw",
        }
    )
    sets.add_skill_to_set(default_set["id"], default_enabled["id"])
    sets.add_skill_to_set(default_set["id"], default_excluded["id"])
    sets.add_default_skill_exclusion(
        user_id="owner-x",
        bot_id="bot-x",
        skill_set_id=int(default_set["id"]),
        skill_id=int(default_excluded["id"]),
    )

    assets = skills.list_bot_active_assets(
        env=default_enabled["env"],
        bot_id="bot-x",
        user_id="owner-x",
        engine="openclaw",
    )

    assert [asset.git_path for asset in assets] == [
        "git://defaults/enabled"
    ]


# ── SkillSetRepository ──────────────────────────────────────────────

def test_skillset_crud(sets):
    rec = sets.create(
        {"name": "SS", "user_id": "5", "bolt_id": "bot1"}
    )
    sid = rec["id"]
    assert sets.get_by_id(sid)["name"] == "SS"
    assert sets.update(sid, {"name": "SS2"})["name"] == "SS2"
    assert sets.delete(sid) is True
    assert sets.get_by_id(sid) is None


def test_add_remove_skill_to_set(skills, sets):
    sk = skills.create({"name": "k", "skill_uuid": "ku",
                        "git_path": "git://k"})
    ss = sets.create({"name": "set"})
    assert sets.add_skill_to_set(ss["id"], sk["id"]) is True
    in_set = sets.get_skills_in_set(ss["id"])
    assert len(in_set) == 1 and in_set[0]["name"] == "k"
    assert sets.remove_skill_from_set(ss["id"], sk["id"]) is True
    assert sets.get_skills_in_set(ss["id"]) == []


def test_get_skills_in_set_center_max_version(skills, sets):
    ss = sets.create({"name": "cset"})
    skills.create(
        {"name": "cv1", "git_path": "center://c", "skill_uuid": "cu",
         "status": "PUBLISHED", "version": 1}
    )
    skills.create(
        {"name": "cv2", "git_path": "center://c", "skill_uuid": "cu",
         "status": "PUBLISHED", "version": 2}
    )
    with skills._db.orm_session() as s:
        s.add(SkillSetSkill(skill_set_id=int(ss["id"]),
                            skill_id=0, skill_uuid="cu"))
    res = sets.get_skills_in_set(ss["id"])
    assert len(res) == 1
    assert res[0]["version"] == 2  # MAX(version) PUBLISHED


def test_set_active_skill_set_clears_then_activates(sets):
    a = sets.create({"name": "A", "bolt_id": "b", "is_active": 1})
    bset = sets.create({"name": "B", "bolt_id": "b"})
    assert sets.set_active_skill_set(bset["id"], bolt_id="b") is True
    assert sets.get_by_id(a["id"])["is_active"] is False
    assert sets.get_by_id(bset["id"])["is_active"] is True
    assert sets.clear_active_skill_set(bolt_id="b") is True
    assert sets.get_by_id(bset["id"])["is_active"] is False


def test_activate_deactivate_skill_set(sets):
    s = sets.create({"name": "X", "bolt_id": "b"})
    assert sets.activate_skill_set(s["id"], bolt_id="b") is True
    assert sets.get_by_id(s["id"])["is_active"] is True
    assert sets.deactivate_skill_set(s["id"], bolt_id="b") is True
    assert sets.get_by_id(s["id"])["is_active"] is False


def test_mcp_in_set(sets):
    ss = sets.create({"name": "m"})
    assert sets.add_mcp_to_set(
        ss["id"], "mcp.x", "X", user_id="u1"
    ) is True
    got = sets.get_mcp_servers_in_set(ss["id"])
    assert len(got) == 1 and got[0]["server_code"] == "mcp.x"
    assert sets.remove_mcp_from_set(ss["id"], "mcp.x") is True
    assert sets.get_mcp_servers_in_set(ss["id"]) == []


def test_explicit_env_active_skill_sets_and_mcps_are_isolated(sets, db):
    pre = sets.create(
        {
            "name": "pre-set",
            "user_id": "172168",
            "bolt_id": "default",
            "engine_type": "openclaw",
            "is_active": True,
        }
    )
    prod = sets.create(
        {
            "name": "prod-set",
            "user_id": "172168",
            "bolt_id": "default",
            "engine_type": "openclaw",
            "is_active": True,
        }
    )
    with db.orm_session() as s:
        s.query(SkillSet).filter(SkillSet.id == int(pre["id"])).update(
            {SkillSet.env: "pre"}
        )
        s.query(SkillSet).filter(SkillSet.id == int(prod["id"])).update(
            {SkillSet.env: "prod"}
        )
    sets.add_mcp_to_set(pre["id"], "mcp.pre", "Pre", env="pre")
    sets.add_mcp_to_set(prod["id"], "mcp.prod", "Prod", env="prod")

    active = sets.get_all_active_skill_sets_for_env(
        user_id="172168",
        bolt_id="default",
        engine_type="openclaw",
        env="prod",
    )

    assert [row["id"] for row in active] == [prod["id"]]
    assert sets.get_mcp_servers_in_set_for_env(prod["id"], env="prod") == [
        {
            **sets.get_mcp_servers_in_set(prod["id"])[0],
            "env": "prod",
        }
    ]
    assert sets.get_mcp_servers_in_set_for_env(pre["id"], env="prod") == []


def test_add_default_mcp_exclusion_upsert_idempotent(sets, db):
    ok = sets.add_default_mcp_exclusion("u1", "bot1", 7, "mcp.z")
    assert ok is True
    # 2nd call on the same uk_user_bot_skillset_mcp → update, no dup.
    sets.add_default_mcp_exclusion("u1", "bot1", 7, "mcp.z")
    with db.orm_session() as s:
        assert s.query(DefaultSkillsetMcpExclusion).count() == 1
    assert sets.get_excluded_mcps("u1", "bot1", 7) == ["mcp.z"]
    assert sets.get_all_excluded_mcps("u1", "bot1") == ["mcp.z"]
    assert sets.remove_default_mcp_exclusion(
        "u1", "bot1", 7, "mcp.z"
    ) is True
    assert sets.get_excluded_mcps("u1", "bot1", 7) == []


# ── DefaultSkillsetSkillExclusion ────────────────────────────────────


def test_add_default_skill_exclusion_upsert_idempotent(sets, db):
    ok = sets.add_default_skill_exclusion("u1", "bot1", 7, 42)
    assert ok is True
    # 2nd call on the same uk_user_bot_skillset_skill → update, no dup.
    sets.add_default_skill_exclusion("u1", "bot1", 7, 42)
    with db.orm_session() as s:
        assert s.query(DefaultSkillsetSkillExclusion).count() == 1
    assert sets.get_excluded_skills("u1", "bot1", 7) == [42]
    assert sets.get_all_excluded_skills("u1", "bot1") == [42]


def test_remove_default_skill_exclusion(sets, db):
    sets.add_default_skill_exclusion("u1", "bot1", 7, 42)
    assert sets.get_excluded_skills("u1", "bot1", 7) == [42]
    assert sets.remove_default_skill_exclusion("u1", "bot1", 7, 42) is True
    assert sets.get_excluded_skills("u1", "bot1", 7) == []
    # Removing non-existent returns False
    assert sets.remove_default_skill_exclusion("u1", "bot1", 7, 42) is False


def test_get_excluded_skills_filters_by_set(sets, db):
    sets.add_default_skill_exclusion("u1", "bot1", 7, 42)
    sets.add_default_skill_exclusion("u1", "bot1", 7, 99)
    sets.add_default_skill_exclusion("u1", "bot1", 8, 42)
    # set 7 has both, set 8 has only 42
    assert sorted(sets.get_excluded_skills("u1", "bot1", 7)) == [42, 99]
    assert sets.get_excluded_skills("u1", "bot1", 8) == [42]


def test_get_all_excluded_skills_cross_set(sets, db):
    sets.add_default_skill_exclusion("u1", "bot1", 7, 42)
    sets.add_default_skill_exclusion("u1", "bot1", 8, 99)
    # Across all sets for (u1, bot1)
    assert sorted(sets.get_all_excluded_skills("u1", "bot1")) == [42, 99]
    # Different bot has no exclusions
    assert sets.get_all_excluded_skills("u1", "bot2") == []


def test_get_excluded_skills_empty(sets, db):
    assert sets.get_excluded_skills("u1", "bot1", 7) == []
    assert sets.get_all_excluded_skills("u1", "bot1") == []


def test_skillset_delete_by_bot_id_cascade(sets, db):
    s1 = sets.create({"name": "s1", "bolt_id": "botz"})
    sets.add_mcp_to_set(s1["id"], "mcp.a", "A")
    with db.orm_session() as s:
        s.add(SkillSetSkill(skill_set_id=int(s1["id"]), skill_id=1))
    assert sets.delete_by_bot_id("botz") == 1
    with db.orm_session() as s:
        assert s.query(SkillSet).count() == 0
        assert s.query(SkillSetSkill).count() == 0
        assert s.query(SkillSetMCPServer).count() == 0
