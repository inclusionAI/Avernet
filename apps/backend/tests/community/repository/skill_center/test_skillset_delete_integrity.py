"""Regression coverage for SkillSet deletion and membership integrity."""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.models import (
    BotSkillInstallation,
    Skill,
    SkillDraftEditLease,
    SkillGrant,
    SkillPublicationAttempt,
    SkillSet,
    SkillSetSkill,
    SkillSpaceBinding,
    SkillVersion,
)
from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import AcSkillMember
from agentclaw.community.core.models.space_skill import SkillDraftUpgradeRequest
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillAssetInUseError,
    SkillSetControlPlaneNotFoundError,
)


class _Database:
    def __init__(self, engine):
        self._sessions = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        with self._sessions.begin() as session:
            yield session

    @contextmanager
    def transactional_orm_session(self):
        with self.orm_session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield session


def test_legacy_skillset_delete_removes_its_memberships(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skillset.db'}")
    for model in (Skill, SkillSet, SkillSetSkill, SkillSetMCPServer):
        model.__table__.create(engine)
    db = _Database(engine)
    sets = SkillSetRepository(db)
    with db.orm_session() as session:
        skill = Skill(name="local", git_path="local:///skill", env="dev")
        skill_set = SkillSet(name="set", bolt_id="bot", env="dev")
        session.add_all([skill, skill_set])
        session.flush()
        set_id = skill_set.id
        session.add(SkillSetSkill(skill_set_id=set_id, skill_id=skill.id, env="dev"))

    assert sets.delete(str(set_id))

    with db.orm_session() as session:
        assert session.query(SkillSetSkill).filter_by(skill_set_id=set_id).count() == 0


def test_legacy_writer_rejects_another_bots_local_skill(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    for model in (Skill, SkillSet, SkillSetSkill, SkillSetMCPServer):
        model.__table__.create(engine)
    db = _Database(engine)
    with db.orm_session() as session:
        skill = Skill(
            name="local",
            git_path="local:///skill",
            bolt_id="bot-a",
            user_id="owner",
            env="dev",
        )
        own = SkillSet(name="own", bolt_id="bot-a", user_id="owner", env="dev")
        foreign = SkillSet(name="foreign", bolt_id="bot-b", user_id="owner", env="dev")
        wrong_owner = SkillSet(
            name="wrong-owner", bolt_id="bot-a", user_id="other", env="dev"
        )
        session.add_all([skill, own, foreign, wrong_owner])
        session.flush()
        skill_id, own_id, foreign_id, wrong_owner_id = (
            skill.id,
            own.id,
            foreign.id,
            wrong_owner.id,
        )

    sets = SkillSetRepository(db)
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        sets.add_skill_to_set(str(foreign_id), str(skill_id))
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        sets.add_skill_to_set(str(wrong_owner_id), str(skill_id))
    assert sets.add_skill_to_set(str(own_id), str(skill_id))
    with db.orm_session() as session:
        assert (
            session.query(SkillSetSkill).filter_by(skill_set_id=foreign_id).count() == 0
        )


def test_skillset_delete_rolls_back_members_when_parent_delete_fails(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rollback.db'}")
    for model in (Skill, SkillSet, SkillSetSkill, SkillSetMCPServer):
        model.__table__.create(engine)
    db = _Database(engine)
    with db.orm_session() as session:
        skill = Skill(name="local", git_path="local:///skill", env="dev")
        skill_set = SkillSet(name="set", bolt_id="bot", env="dev")
        session.add_all([skill, skill_set])
        session.flush()
        set_id = skill_set.id
        session.add(SkillSetSkill(skill_set_id=set_id, skill_id=skill.id, env="dev"))

    def fail_parent_delete(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith("delete from ac_skill_set "):
            raise RuntimeError("injected parent delete failure")

    event.listen(engine, "before_cursor_execute", fail_parent_delete)
    with pytest.raises(RuntimeError, match="injected parent delete failure"):
        SkillSetRepository(db).delete(str(set_id))
    event.remove(engine, "before_cursor_execute", fail_parent_delete)

    with db.orm_session() as session:
        assert session.query(SkillSet).filter_by(id=set_id).count() == 1
        assert session.query(SkillSetSkill).filter_by(skill_set_id=set_id).count() == 1


def test_bot_deletion_retains_skill_with_historical_orphan_membership(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'bot.db'}")
    for model in (
        Skill,
        SkillSet,
        SkillSetSkill,
        SkillSetMCPServer,
        BotSkillInstallation,
        SkillDraftEditLease,
        SkillGrant,
        SkillPublicationAttempt,
        SkillSpaceBinding,
        SkillVersion,
        SkillDraftUpgradeRequest,
        AcSkillMember,
    ):
        model.__table__.create(engine)
    db = _Database(engine)
    with db.orm_session() as session:
        skill = Skill(
            name="local",
            git_path="local:///skill",
            bolt_id="bot",
            user_id="owner",
            env="dev",
        )
        session.add(skill)
        session.flush()
        skill_id = skill.id
        # A membership can remain even after its set was already deleted.
        session.add(SkillSetSkill(skill_set_id=42, skill_id=skill_id, env="dev"))

    with pytest.raises(SkillAssetInUseError):
        SkillRepository(db).delete_by_bot_id("bot", "owner")
    with db.orm_session() as session:
        assert session.query(Skill).filter_by(id=skill_id).count() == 1
        # The orphan's Set cannot be scoped to a Bot and owner. Historical
        # membership cleanup is deliberately outside this operation.
        assert session.query(SkillSetSkill).filter_by(skill_id=skill_id).count() == 1


def test_owned_bot_cleanup_with_sqlite_foreign_keys_enabled(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fk.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    for model in (
        Skill,
        SkillSet,
        SkillSetSkill,
        SkillSetMCPServer,
        BotSkillInstallation,
        SkillDraftEditLease,
        SkillGrant,
        SkillPublicationAttempt,
        SkillSpaceBinding,
        SkillVersion,
        SkillDraftUpgradeRequest,
        AcSkillMember,
    ):
        model.__table__.create(engine)
    db = _Database(engine)
    with db.orm_session() as session:
        skill = Skill(
            name="local",
            git_path="local:///skill",
            bolt_id="bot",
            user_id="owner",
            env="dev",
        )
        skill_set = SkillSet(name="own", bolt_id="bot", user_id="owner", env="dev")
        session.add_all([skill, skill_set])
        session.flush()
        skill_id = skill.id
        session.add(
            SkillSetSkill(skill_set_id=skill_set.id, skill_id=skill_id, env="dev")
        )
    assert SkillSetRepository(db).delete_by_bot_id("bot", "owner") == 1
    assert SkillRepository(db).delete_by_bot_id("bot", "owner") == 1
    with db.orm_session() as session:
        assert session.query(Skill).filter_by(id=skill_id).count() == 0


def test_bot_cleanup_rolls_back_all_assets_on_unexpected_write_failure(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'failure.db'}")
    for model in (
        Skill,
        SkillSet,
        SkillSetSkill,
        SkillSetMCPServer,
        BotSkillInstallation,
        SkillDraftEditLease,
        SkillGrant,
        SkillPublicationAttempt,
        SkillSpaceBinding,
        SkillVersion,
        SkillDraftUpgradeRequest,
        AcSkillMember,
    ):
        model.__table__.create(engine)
    db = _Database(engine)
    with db.orm_session() as session:
        session.add_all(
            [
                Skill(
                    name="one",
                    git_path="local:///one",
                    bolt_id="bot",
                    user_id="owner",
                    env="dev",
                ),
                Skill(
                    name="two",
                    git_path="local:///two",
                    bolt_id="bot",
                    user_id="owner",
                    env="dev",
                ),
            ]
        )
    calls = 0

    def fail_second_delete(conn, cursor, statement, parameters, context, executemany):
        nonlocal calls
        if statement.lstrip().lower().startswith("delete from ac_skill "):
            calls += 1
            if calls == 2:
                raise RuntimeError("injected write failure")

    event.listen(engine, "before_cursor_execute", fail_second_delete)
    with pytest.raises(RuntimeError, match="injected write failure"):
        SkillRepository(db).cleanup_by_bot_id("bot", "owner")
    event.remove(engine, "before_cursor_execute", fail_second_delete)
    with db.orm_session() as session:
        assert session.query(Skill).count() == 2


@pytest.mark.parametrize(
    "set_bot,set_owner",
    [("bot-b", "owner"), ("default", "owner"), ("bot-a", None)],
)
def test_bot_deletion_does_not_remove_another_bots_membership(
    tmp_path, set_bot, set_owner
):
    engine = create_engine(f"sqlite:///{tmp_path / 'foreign.db'}")
    for model in (
        Skill,
        SkillSet,
        SkillSetSkill,
        SkillSetMCPServer,
        BotSkillInstallation,
        SkillDraftEditLease,
        SkillGrant,
        SkillPublicationAttempt,
        SkillSpaceBinding,
        SkillVersion,
        SkillDraftUpgradeRequest,
        AcSkillMember,
    ):
        model.__table__.create(engine)
    db = _Database(engine)
    with db.orm_session() as session:
        skill = Skill(
            name="local",
            git_path="local:///skill",
            bolt_id="bot-a",
            user_id="owner",
            env="dev",
        )
        foreign = SkillSet(
            name="foreign", bolt_id=set_bot, user_id=set_owner, env="dev"
        )
        free_skill = Skill(
            name="free",
            git_path="local:///free",
            bolt_id="bot-a",
            user_id="owner",
            env="dev",
        )
        session.add_all([skill, foreign, free_skill])
        session.flush()
        skill_id = skill.id
        free_skill_id = free_skill.id
        session.add(
            SkillSetSkill(skill_set_id=foreign.id, skill_id=skill_id, env="dev")
        )

    with pytest.raises(SkillAssetInUseError):
        SkillRepository(db).delete_by_bot_id("bot-a", "owner")
    result = SkillRepository(db).cleanup_by_bot_id("bot-a", "owner")
    assert result == {"deleted": 1, "retained": 1, "blocker_counts": {"membership": 1}}
    with db.orm_session() as session:
        assert session.query(Skill).filter_by(id=skill_id).count() == 1
        assert session.query(Skill).filter_by(id=free_skill_id).count() == 0
        assert session.query(SkillSetSkill).filter_by(skill_id=skill_id).count() == 1


@pytest.mark.parametrize(
    "set_bot,set_owner",
    [
        ("default", "owner"),
        ("bot-a", None),
        ("bot-a", "other-owner"),
        (None, None),
    ],
)
def test_bot_cleanup_preserves_set_outside_exact_ownership(
    tmp_path, set_bot, set_owner
):
    engine = create_engine(f"sqlite:///{tmp_path / 'scope.db'}")
    for model in (Skill, SkillSet, SkillSetSkill, SkillSetMCPServer):
        model.__table__.create(engine)
    db = _Database(engine)
    with db.orm_session() as session:
        own = SkillSet(name="owned", bolt_id="bot-a", user_id="owner", env="dev")
        other = SkillSet(name="other", bolt_id=set_bot, user_id=set_owner, env="dev")
        session.add_all([own, other])
        session.flush()
        other_id = other.id
    assert SkillSetRepository(db).delete_by_bot_id("bot-a", "owner") == 1
    with db.orm_session() as session:
        assert session.query(SkillSet).filter_by(id=other_id).count() == 1
