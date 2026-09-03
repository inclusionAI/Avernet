"""Persistence tests for Offline blocker inventory and atomic commit."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.space_skill import (
    SkillGrant,
    SkillPublicationAttempt,
    SkillSpaceBinding,
    SkillVersion,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_offline import (
    SpaceSkillOfflineRepository,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel


_UUID = "11111111-1111-4111-8111-111111111111"


class _Database:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(bind=self.engine)

    @contextmanager
    def orm_session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    transactional_orm_session = orm_session


def _seed(db: _Database):
    with db.orm_session() as session:
        space = SpaceModel(
            space_code="team-offline",
            space_type="TEAM",
            name="Offline Space",
            created_by="owner-1",
            updated_by="owner-1",
            env="test",
        )
        session.add(space)
        session.flush()
        skill = Skill(
            name="offline-skill",
            description="published",
            git_path=f"center://{_UUID}",
            status="PUBLISHED",
            env="test",
            skill_uuid=_UUID,
        )
        session.add(skill)
        session.flush()
        session.add_all(
            [
                SkillSpaceBinding(
                    skill_id=skill.id,
                    space_id=space.id,
                    created_by="owner-1",
                    env="test",
                ),
                SkillGrant(
                    skill_id=skill.id,
                    user_id="owner-1",
                    role="OWNER",
                    status="ACTIVE",
                    owner_slot=1,
                    granted_by="owner-1",
                    env="test",
                ),
                SkillVersion(
                    skill_id=skill.id,
                    version_ordinal=2,
                    status="PUBLISHED",
                    sc_version_number="2.0.0",
                    name="offline-skill",
                    description="published",
                    created_by="owner-1",
                    env="test",
                ),
            ]
        )
        return int(space.id), int(skill.id)


def test_inspection_includes_inactive_membership_installation_and_attempt():
    db = _Database()
    space_id, skill_id = _seed(db)
    with db.orm_session() as session:
        skill_set = SkillSet(
            name="Inactive Set",
            user_id="owner-1",
            bolt_id="service-a",
            is_active=False,
            is_default=False,
            env="test",
        )
        default_set = SkillSet(
            name="Default Set",
            user_id="",
            bolt_id="",
            is_active=True,
            is_default=True,
            env="test",
        )
        session.add_all((skill_set, default_set))
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=skill_set.id,
                    skill_id=skill_id,
                    env="test",
                ),
                SkillSetSkill(
                    skill_set_id=default_set.id,
                    skill_id=skill_id,
                    env="test",
                ),
                BotSkillInstallation(
                    bot_id="service-a",
                    owner_id="owner-1",
                    skill_id=skill_id,
                    env="test",
                ),
                SkillPublicationAttempt(
                    skill_id=skill_id,
                    request_id="attempt-1",
                    active_skill_key=f"test:{skill_id}",
                    target_version_ordinal=3,
                    status="RESULT_UNKNOWN",
                    created_by="owner-1",
                    env="test",
                ),
            ]
        )
    inspection = SpaceSkillOfflineRepository(db).inspect(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        env="test",
    )

    assert [(item.target_version_ordinal, item.status) for item in inspection.publication_attempts] == [
        (3, "RESULT_UNKNOWN")
    ]
    assert {item.skill_set_name for item in inspection.memberships} == {
        "Inactive Set",
        "Default Set",
    }
    assert [(item.bot_id) for item in inspection.installations] == ["service-a"]
    assert inspection.space_bound is True
    assert inspection.actor_roles == ("OWNER",)


def test_offline_commit_preserves_published_version_without_creating_a_draft():
    db = _Database()
    space_id, skill_id = _seed(db)
    repo = SpaceSkillOfflineRepository(db)

    result = repo.commit(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        env="test",
        guard=lambda _inspection: None,
    )

    assert result.changed is True
    with db.orm_session() as session:
        skill = session.query(Skill).filter_by(id=skill_id).one()
        assert skill.status == "PUBLISHED"
        assert skill.offline_at is not None
        assert skill.offline_by == "owner-1"
        assert skill.draft_target_version is None
        assert skill.draft_status is None
        assert skill.zip_url is None
        assert session.query(SkillVersion).filter_by(skill_id=skill_id).count() == 1


def test_transaction_guard_runs_after_locked_db_recheck_and_can_abort():
    db = _Database()
    space_id, skill_id = _seed(db)
    repo = SpaceSkillOfflineRepository(db)

    def _guard(locked):
        assert locked.identity.skill_id == skill_id
        assert locked.space_bound is True
        assert locked.actor_roles == ("OWNER",)
        raise RuntimeError("artifact guard blocked")

    with pytest.raises(RuntimeError, match="artifact guard blocked"):
        repo.commit(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner-1",
            env="test",
            guard=_guard,
        )

    with db.orm_session() as session:
        skill = session.query(Skill).filter_by(id=skill_id).one()
        assert skill.offline_at is None
        assert skill.draft_status is None
