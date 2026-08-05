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
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.models import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.skill_center.local_skill_cleanup import (
    LocalSkillCleanupWorkModel,
)
from agentclaw.community.core.skill_center.services.repositories import (
    ActiveSkillSetReferenceError,
)
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
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self.engine = engine
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

    def transactional_orm_session(self):
        return self.orm_session()

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
        LocalSkillCleanupWorkModel,
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


def test_delete_removes_all_associations_for_active_and_inactive_local_skills(
    skills, sets, db
):
    active_skill = skills.create(
        {"name": "active-local", "git_path": "local:///skills/active"}
    )
    inactive_skill = skills.create(
        {"name": "inactive-local", "git_path": "local:///skills/inactive"}
    )
    active_set = sets.create({"name": "active", "is_active": True})
    inactive_set = sets.create({"name": "inactive", "is_active": False})
    extra_set = sets.create({"name": "extra", "is_active": True})
    sets.add_skill_to_set(active_set["id"], active_skill["id"])
    sets.add_skill_to_set(extra_set["id"], active_skill["id"])
    sets.add_skill_to_set(inactive_set["id"], inactive_skill["id"])

    assert skills.delete(active_skill["id"]) is True
    assert skills.delete(inactive_skill["id"]) is True

    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 0
        assert session.query(Skill).count() == 0


def test_delete_succeeds_for_skill_without_an_association(skills, db):
    skill = skills.create(
        {"name": "unassociated", "git_path": "local:///skills/unassociated"}
    )

    assert skills.delete(skill["id"]) is True

    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 0
        assert session.query(Skill).count() == 0


def test_delete_rolls_back_when_association_cleanup_fails(skills, sets, db):
    skill = skills.create({"name": "rollback-association"})
    skill_set = sets.create({"name": "set"})
    sets.add_skill_to_set(skill_set["id"], skill["id"])

    def fail_association_delete(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ):
        if statement.lstrip().lower().startswith("delete from ac_skill_set_skill"):
            raise RuntimeError("injected association delete failure")

    event.listen(db.engine, "before_cursor_execute", fail_association_delete)
    try:
        with pytest.raises(RuntimeError, match="injected association delete failure"):
            skills.delete(skill["id"])
    finally:
        event.remove(db.engine, "before_cursor_execute", fail_association_delete)

    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 1
        assert session.query(Skill).count() == 1


def test_delete_rolls_back_association_cleanup_when_skill_delete_fails(
    skills, sets, db
):
    skill = skills.create({"name": "rollback-skill"})
    skill_set = sets.create({"name": "set"})
    sets.add_skill_to_set(skill_set["id"], skill["id"])

    def fail_skill_delete(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ):
        if statement.lstrip().lower().startswith("delete from ac_skill "):
            raise RuntimeError("injected skill delete failure")

    event.listen(db.engine, "before_cursor_execute", fail_skill_delete)
    try:
        with pytest.raises(RuntimeError, match="injected skill delete failure"):
            skills.delete(skill["id"])
    finally:
        event.remove(db.engine, "before_cursor_execute", fail_skill_delete)

    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 1
        assert session.query(Skill).count() == 1


def test_delete_cannot_remove_a_skill_or_association_from_another_tenant(
    skills, sets, db
):
    with avernet_tenant_scope("tenant-b"):
        skill = skills.create({"name": "tenant-b-skill"})
        skill_set = sets.create({"name": "tenant-b-set"})
        sets.add_skill_to_set(skill_set["id"], skill["id"])

    with avernet_tenant_scope("tenant-a"):
        assert skills.delete(skill["id"]) is False
        with db.orm_session() as session:
            assert session.query(SkillSetSkill).count() == 0
            assert session.query(Skill).count() == 0

    with avernet_tenant_scope("tenant-b"):
        with db.orm_session() as session:
            assert session.query(SkillSetSkill).count() == 1
            assert session.query(Skill).count() == 1


def test_public_local_delete_commits_cleanup_work_with_derived_state(skills, sets, db):
    from agentclaw.community.plugins.local_skill_cleanup_repository import (
        SqlLocalSkillCleanupRepository,
    )

    with avernet_tenant_scope("tenant-a"):
        skill = skills.create(
            {"name": "local", "git_path": "local:///skills/local", "user_id": "owner", "bolt_id": "bot"}
        )
        default_set = sets.create(
            {"name": "default", "user_id": "owner", "bolt_id": "bot", "is_default": True}
        )
        sets.add_skill_to_set(default_set["id"], skill["id"], user_id="owner")
        sets.add_default_skill_exclusion("owner", "bot", int(default_set["id"]), int(skill["id"]))
        cleanup_work_id = SqlLocalSkillCleanupRepository(db).record_preparing(
            env="dev", owner_id="owner", bot_id="bot", skill_id=skill["id"],
            package_locator="/skills/.local.delete-verified",
        )
        assert SqlLocalSkillCleanupRepository(db).record_repair_required(
            env="dev", owner_id="owner", bot_id="bot", skill_id=skill["id"],
            package_locator="/skills/.local.delete-verified",
        ) == cleanup_work_id
        work_id = skills.delete_bot_local_skill(
            skill_id=skill["id"], owner_id="owner", bot_id="bot",
            quarantine_locator="/skills/.local.delete-verified",
            cleanup_work_id=cleanup_work_id,
        )
        assert work_id is not None
        with db.orm_session() as session:
            work = session.query(LocalSkillCleanupWorkModel).one()
            assert work.id == work_id
            assert work.status == "pending"
            assert work.package_locator == "/skills/.local.delete-verified"
            assert session.query(Skill).count() == 0
            assert session.query(SkillSetSkill).count() == 0
            assert session.query(DefaultSkillsetSkillExclusion).count() == 0


def test_public_local_replace_commits_locator_and_cleanup_work_atomically(skills, db):
    from agentclaw.community.plugins.local_skill_cleanup_repository import (
        SqlLocalSkillCleanupRepository,
    )

    with avernet_tenant_scope("tenant-a"):
        skill = skills.create(
            {
                "name": "local",
                "description": "old",
                "git_path": "local:///skills/local",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        cleanup_work_id = SqlLocalSkillCleanupRepository(db).record_preparing(
            env="dev",
            owner_id="owner",
            bot_id="bot",
            skill_id=skill["id"],
            package_locator="/skills/local",
        )

        assert skills.replace_bot_local_skill(
            skill_id=skill["id"],
            owner_id="owner",
            bot_id="bot",
            old_locator="/skills/local",
            new_locator="/skills/.local.replacement-1",
            description="new",
            requires_runtime_restore=True,
            cleanup_work_id=cleanup_work_id,
        ) == cleanup_work_id

        with db.orm_session() as session:
            persisted = session.query(Skill).one()
            cleanup = session.query(LocalSkillCleanupWorkModel).one()
            assert persisted.git_path == "local:///skills/.local.replacement-1"
            assert persisted.description == "new"
            assert cleanup.status == "pending"
            assert cleanup.requires_runtime_restore == 1


def test_public_local_replace_rolls_back_locator_when_cleanup_commit_fails(
    skills, db
):
    from agentclaw.community.plugins.local_skill_cleanup_repository import (
        SqlLocalSkillCleanupRepository,
    )

    with avernet_tenant_scope("tenant-a"):
        skill = skills.create(
            {
                "name": "local",
                "description": "old",
                "git_path": "local:///skills/local",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        cleanup_work_id = SqlLocalSkillCleanupRepository(db).record_preparing(
            env="dev",
            owner_id="owner",
            bot_id="bot",
            skill_id=skill["id"],
            package_locator="/skills/local",
        )

        def fail_cleanup_update(
            _conn, _cursor, statement, _parameters, _context, _executemany
        ):
            if statement.lstrip().lower().startswith(
                "update ac_local_skill_cleanup_work"
            ):
                raise RuntimeError("injected cleanup commit failure")

        event.listen(db.engine, "before_cursor_execute", fail_cleanup_update)
        try:
            with pytest.raises(RuntimeError, match="injected cleanup commit failure"):
                skills.replace_bot_local_skill(
                    skill_id=skill["id"],
                    owner_id="owner",
                    bot_id="bot",
                    old_locator="/skills/local",
                    new_locator="/skills/.local.replacement-1",
                    description="new",
                    requires_runtime_restore=False,
                    cleanup_work_id=cleanup_work_id,
                )
        finally:
            event.remove(db.engine, "before_cursor_execute", fail_cleanup_update)

        with db.orm_session() as session:
            persisted = session.query(Skill).one()
            cleanup = session.query(LocalSkillCleanupWorkModel).one()
            assert persisted.git_path == "local:///skills/local"
            assert persisted.description == "old"
            assert cleanup.status == "preparing"


def test_public_local_delete_rolls_back_cleanup_work_with_all_derived_state(skills, sets, db):
    from agentclaw.community.plugins.local_skill_cleanup_repository import (
        SqlLocalSkillCleanupRepository,
    )

    with avernet_tenant_scope("tenant-a"):
        skill = skills.create(
            {"name": "local", "git_path": "local:///skills/local", "user_id": "owner", "bolt_id": "bot"}
        )
        default_set = sets.create(
            {"name": "default", "user_id": "owner", "bolt_id": "bot", "is_default": True}
        )
        sets.add_skill_to_set(default_set["id"], skill["id"], user_id="owner")
        sets.add_default_skill_exclusion("owner", "bot", int(default_set["id"]), int(skill["id"]))

        cleanup_work_id = SqlLocalSkillCleanupRepository(db).record_preparing(
            env="dev", owner_id="owner", bot_id="bot", skill_id=skill["id"],
            package_locator="/skills/.local.delete-rollback",
        )

        def fail_skill_delete(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().lower().startswith("delete from ac_skill "):
                raise RuntimeError("injected skill delete failure")

        event.listen(db.engine, "before_cursor_execute", fail_skill_delete)
        try:
            with pytest.raises(RuntimeError, match="injected skill delete failure"):
                skills.delete_bot_local_skill(
                    skill_id=skill["id"], owner_id="owner", bot_id="bot",
                    quarantine_locator="/skills/.local.delete-rollback",
                    cleanup_work_id=cleanup_work_id,
                )
        finally:
            event.remove(db.engine, "before_cursor_execute", fail_skill_delete)

        with db.orm_session() as session:
            assert session.query(Skill).count() == 1
            assert session.query(SkillSetSkill).count() == 1
            assert session.query(DefaultSkillsetSkillExclusion).count() == 1
            cleanup = session.query(LocalSkillCleanupWorkModel).one()
            assert cleanup.status == "preparing"


def test_public_local_delete_rechecks_active_custom_set_in_delete_transaction(
    skills, sets, db
):
    from agentclaw.community.plugins.local_skill_cleanup_repository import (
        SqlLocalSkillCleanupRepository,
    )

    with avernet_tenant_scope("tenant-a"):
        skill = skills.create({
            "name": "local", "git_path": "local:///skills/local",
            "user_id": "owner", "bolt_id": "bot",
        })
        active_set = sets.create({
            "name": "active-custom", "user_id": "owner", "bolt_id": "bot",
            "is_default": False, "is_active": True,
        })
        sets.add_skill_to_set(active_set["id"], skill["id"], user_id="owner")
        locator = "/skills/.local.delete-active-race"
        cleanup_work_id = SqlLocalSkillCleanupRepository(db).record_preparing(
            env="dev", owner_id="owner", bot_id="bot", skill_id=skill["id"],
            package_locator=locator,
        )

        with pytest.raises(ActiveSkillSetReferenceError):
            skills.delete_bot_local_skill(
                skill_id=skill["id"], owner_id="owner", bot_id="bot",
                quarantine_locator=locator, cleanup_work_id=cleanup_work_id,
            )

        with db.orm_session() as session:
            assert session.query(Skill).count() == 1
            assert session.query(SkillSetSkill).count() == 1
            assert session.query(LocalSkillCleanupWorkModel).one().status == "preparing"


def test_skill_create_is_plain_insert_not_upsert(skills):
    # Distinct versions → two independent rows (plain INSERT, not an
    # upsert that would update-in-place).
    a = skills.create({"name": "dup", "skill_uuid": "x", "version": 1})
    b = skills.create({"name": "dup", "skill_uuid": "x", "version": 2})
    assert a["id"] != b["id"]
    assert len(skills.list_skills()) == 2


def test_skill_create_matches_prod_without_a_source_only_unique_constraint(skills):
    """Prod accepts duplicate legacy skill identity fields; SQLite must too."""
    first = skills.create({"name": "d", "skill_uuid": "x", "version": 1})
    duplicate = skills.create({"name": "d", "skill_uuid": "x", "version": 1})
    assert duplicate["id"] != first["id"]


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


def test_list_skill_set_references_includes_active_and_inactive_sets(
    skills, sets
):
    skill = skills.create({"name": "referenced", "bolt_id": "bot-x"})
    active_set = sets.create(
        {"name": "active", "bolt_id": "bot-x", "is_active": True}
    )
    inactive_set = sets.create(
        {"name": "inactive", "bolt_id": "bot-x", "is_active": False}
    )
    sets.add_skill_to_set(active_set["id"], skill["id"])
    sets.add_skill_to_set(inactive_set["id"], skill["id"])

    assert skills.list_skill_set_references(skill["id"]) == [
        {"skill_set_id": active_set["id"]},
        {"skill_set_id": inactive_set["id"]},
    ]


def test_list_skill_set_references_ignores_orphans_and_matches_center_uuid(
    skills, sets, db
):
    center = skills.create(
        {
            "name": "center",
            "git_path": "center://center",
            "skill_uuid": "center-uuid",
        }
    )
    live_set = sets.create({"name": "live", "is_active": False})
    deleted_set = sets.create({"name": "deleted", "is_active": False})
    with db.orm_session() as session:
        session.add(
            SkillSetSkill(
                skill_set_id=int(live_set["id"]),
                skill_id=0,
                skill_uuid="center-uuid",
            )
        )
        session.add(
            SkillSetSkill(
                skill_set_id=int(deleted_set["id"]),
                skill_id=int(center["id"]),
            )
        )
    assert sets.delete(deleted_set["id"]) is True

    assert skills.list_skill_set_references(
        center["id"],
        skill_uuid="center-uuid",
    ) == [{"skill_set_id": live_set["id"]}]


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


def test_get_all_active_skill_sets_preserves_global_and_bot_scoped_defaults(sets):
    bot_default = sets.create(
        {
            "name": "bot defaults",
            "user_id": "owner-x",
            "bolt_id": "bot-x",
            "engine_type": "openclaw",
            "is_default": True,
            "is_active": True,
        }
    )
    global_default = sets.create(
        {
            "name": "global defaults",
            "engine_type": "openclaw",
            "is_default": True,
            "is_active": True,
        }
    )

    active = sets.get_all_active_skill_sets(
        user_id="owner-x", bolt_id="bot-x", engine_type="openclaw"
    )

    assert [row["id"] for row in active] == [
        bot_default["id"],
        global_default["id"],
    ]


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


def test_env_scoped_active_skill_sets_preserve_bot_and_global_defaults(sets, db):
    bot_default = sets.create(
        {
            "name": "bot defaults",
            "user_id": "owner-x",
            "bolt_id": "bot-x",
            "engine_type": "openclaw",
            "is_default": True,
            "is_active": True,
        }
    )
    global_default = sets.create(
        {
            "name": "global defaults",
            "engine_type": "openclaw",
            "is_default": True,
            "is_active": True,
        }
    )
    with db.orm_session() as session:
        session.query(SkillSet).filter(SkillSet.id.in_([
            int(bot_default["id"]), int(global_default["id"])
        ])).update({SkillSet.env: "prod"}, synchronize_session=False)

    active = sets.get_all_active_skill_sets_for_env(
        user_id="owner-x", bolt_id="bot-x", engine_type="openclaw", env="prod"
    )

    assert [row["id"] for row in active] == [
        bot_default["id"],
        global_default["id"],
    ]


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
