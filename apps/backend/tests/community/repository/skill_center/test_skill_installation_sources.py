"""Default withdrawal is source-aware, scoped, and preserves direct intent."""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import BotSkillInstallation, Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)


class Database:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def transactional_orm_session(self):
        with self.sessions() as session:
            with session.begin():
                yield session

    orm_session = transactional_orm_session


SCOPE = dict(bot_id="default", owner_id="owner", engine_type="openclaw", default_engine_types=("openclaw",))


def seed(db, *, installed=False):
    with db.orm_session() as session:
        skill = Skill(name="shared", git_path="git://infra/shared", env="dev")
        default = SkillSet(name="default", is_default=True, user_id="", bolt_id="", engine_type="openclaw", env="dev")
        session.add_all([skill, default])
        session.flush()
        session.add(SkillSetSkill(skill_set_id=default.id, skill_id=skill.id, env="dev"))
        if installed:
            session.add(BotSkillInstallation(bot_id="default", owner_id="owner", skill_id=skill.id, env="dev"))
        return skill.id, default.id


def installed(db, owner="owner"):
    with db.orm_session() as session:
        return {row.skill_id for row in session.query(BotSkillInstallation).filter_by(bot_id="default", owner_id=owner, env="dev")}


@pytest.mark.parametrize("method", ["flush_installations", "sync_default_installations"])
def test_removed_default_member_is_uninstalled_on_next_effective_read(method):
    db = Database()
    skill_id, default_id = seed(db)
    repo = CapabilityDesiredStateRepository(db)
    repo.initialize_installations(env="dev", **SCOPE)
    assert installed(db) == {skill_id}
    with db.orm_session() as session:
        session.query(SkillSetSkill).filter_by(skill_set_id=default_id).delete()
    getattr(repo, method)(env="dev", **SCOPE)
    assert installed(db) == set()


def test_unknown_historical_installation_is_not_adopted_as_default():
    db = Database()
    skill_id, default_id = seed(db, installed=True)
    repo = CapabilityDesiredStateRepository(db)
    repo.sync_default_installations(env="dev", **SCOPE)
    with db.orm_session() as session:
        session.query(SkillSetSkill).filter_by(skill_set_id=default_id).delete()
    repo.sync_default_installations(env="dev", **SCOPE)
    assert installed(db) == {skill_id}


def test_direct_claim_after_default_removal_preserves_existing_installation():
    db = Database()
    skill_id, default_id = seed(db)
    repo = CapabilityDesiredStateRepository(db)
    repo.initialize_installations(env="dev", **SCOPE)
    with db.orm_session() as session:
        session.query(SkillSetSkill).filter_by(skill_set_id=default_id).delete()
    repo.install_skill(skill_id=str(skill_id), **SCOPE)
    repo.sync_default_installations(env="dev", **SCOPE)
    assert installed(db) == {skill_id}


def test_direct_before_default_materialization_is_not_relabelled():
    db = Database()
    skill_id, default_id = seed(db)
    with db.orm_session() as session:
        membership = session.query(SkillSetSkill).filter_by(skill_set_id=default_id).one()
        session.delete(membership)
    repo = CapabilityDesiredStateRepository(db)
    repo.install_skill(skill_id=str(skill_id), **SCOPE)
    with db.orm_session() as session:
        session.add(SkillSetSkill(skill_set_id=default_id, skill_id=skill_id, env="dev"))
    repo.sync_default_installations(env="dev", **SCOPE)
    with db.orm_session() as session:
        session.query(SkillSetSkill).filter_by(skill_set_id=default_id).delete()
    repo.flush_installations(env="dev", **SCOPE)
    assert installed(db) == {skill_id}


def test_default_withdrawal_is_exact_owner_scoped_and_origin_restores_with_snapshot():
    db = Database()
    skill_id, default_id = seed(db)
    repo = CapabilityDesiredStateRepository(db)
    repo.initialize_installations(env="dev", **SCOPE)
    repo.initialize_installations(env="dev", **{**SCOPE, "owner_id": "other"})
    snapshot = repo.snapshot_desired_state(bot_id="default", owner_id="owner", engine_type="openclaw")
    assert snapshot.installation_sources[skill_id] == ("DEFAULT", default_id)
    repo.purge_bot_installations(bot_id="default", owner_id="owner", env="dev")
    repo.restore_desired_state(bot_id="default", owner_id="owner", state=snapshot, engine_type="openclaw")
    with db.orm_session() as session:
        session.query(SkillSetSkill).filter_by(skill_set_id=default_id).delete()
    repo.sync_default_installations(env="dev", **SCOPE)
    assert installed(db) == set()
    assert installed(db, "other") == {skill_id}


def test_source_failure_rolls_back_installation(monkeypatch):
    from sqlalchemy import event
    from agentclaw.community.core.models.skill_installation_source import BotSkillInstallationSource
    db = Database()
    seed(db)
    repo = CapabilityDesiredStateRepository(db)
    def deny_source(*args):
        raise RuntimeError("origin persistence failed")
    event.listen(BotSkillInstallationSource, "before_insert", deny_source)
    try:
        with pytest.raises(RuntimeError, match="origin persistence failed"):
            repo.initialize_installations(env="dev", **SCOPE)
        assert installed(db) == set()
    finally:
        event.remove(BotSkillInstallationSource, "before_insert", deny_source)


@pytest.mark.parametrize("method", ["flush_installations", "sync_default_installations"])
def test_default_source_in_another_env_cannot_delete_direct(method):
    from agentclaw.community.core.models.skill_installation_source import SkillInstallationSourceKind
    from agentclaw.community.core.repository.implementations.skill_center.tables import skill_installations

    db = Database()
    skill_id, default_id = seed(db)
    repo = CapabilityDesiredStateRepository(db)
    repo.initialize_installations(env="dev", **SCOPE)
    with db.orm_session() as session:
        skill_installations.install(
            session, bot_id="default", owner_id="owner", env="pre", skill_id=skill_id,
            source_kind=SkillInstallationSourceKind.DIRECT,
        )
        session.query(SkillSetSkill).filter_by(skill_set_id=default_id).delete()
    getattr(repo, method)(env="pre", **SCOPE)
    with db.orm_session() as session:
        assert skill_installations.installed_ids(
            session, bot_id="default", owner_id="owner", env="pre",
        ) == {skill_id}
