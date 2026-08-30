"""Persistence tests for Offline blocker inventory and atomic commit."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.api.service_artifact_lineage import (
    ServiceArtifactLineage,
    ServiceArtifactReference,
    UnknownServiceArtifact,
)
from agentclaw.community.api.space_skill_offline_service import OfflineBlockerKind
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
from agentclaw.community.core.skill_center.errors import SkillOfflineBlockedError
from agentclaw.community.core.spaces.repository.models import SpaceModel


_UUID = "11111111-1111-4111-8111-111111111111"
_REV = "22222222-2222-4222-8222-222222222222"


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


class _Lineage:
    def __init__(self, *answers: ServiceArtifactLineage) -> None:
        self.answers = list(answers) or [ServiceArtifactLineage((), ())]

    def scan(self, *, skill_uuid, env):
        assert skill_uuid == _UUID
        assert env == "test"
        if len(self.answers) > 1:
            return self.answers.pop(0)
        return self.answers[0]


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


def test_inspection_includes_inactive_membership_installation_attempt_and_unknown_artifact():
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
        session.add(skill_set)
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=skill_set.id,
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
    lineage = _Lineage(
        ServiceArtifactLineage(
            (),
            (
                UnknownServiceArtifact(
                    resource_id="artifact-scan",
                    display_name="scan incomplete",
                ),
            ),
        )
    )

    inspection = SpaceSkillOfflineRepository(db, lineage).inspect(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        env="test",
    )

    assert [item.kind for item in inspection.blockers] == [
        OfflineBlockerKind.PUBLICATION,
        OfflineBlockerKind.MEMBERSHIP,
        OfflineBlockerKind.INSTALLATION,
        OfflineBlockerKind.UNKNOWN_ARTIFACT,
    ]


def test_offline_commit_preserves_published_version_and_creates_editing_vn_plus_one():
    db = _Database()
    space_id, skill_id = _seed(db)
    repo = SpaceSkillOfflineRepository(db, _Lineage())
    identity = repo.inspect(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="test"
    ).identity

    result = repo.commit(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        expected_version_id=identity.latest_version_id,
        target_version=3,
        new_locator=f"draft://{_UUID}/v3/{_REV}",
        new_description="published",
        env="test",
    )

    assert result.changed is True
    with db.orm_session() as session:
        skill = session.query(Skill).filter_by(id=skill_id).one()
        assert skill.status == "PUBLISHED"
        assert skill.offline_at is not None
        assert skill.offline_by == "owner-1"
        assert skill.draft_target_version == 3
        assert skill.draft_status == "EDITING"
        assert session.query(SkillVersion).filter_by(skill_id=skill_id).count() == 1


def test_artifact_inserted_after_preview_blocks_transactional_recheck():
    db = _Database()
    space_id, skill_id = _seed(db)
    empty = ServiceArtifactLineage((), ())
    new_artifact = ServiceArtifactLineage(
        (
            ServiceArtifactReference(
                publish_id=88,
                source_bot_id="service-a",
                source_bot_name="Service A",
                service_version=4,
                sc_version_number="2.0.0",
            ),
        ),
        (),
    )
    repo = SpaceSkillOfflineRepository(db, _Lineage(empty, new_artifact))
    identity = repo.inspect(
        space_id=space_id, skill_id=skill_id, actor_id="owner-1", env="test"
    ).identity

    with pytest.raises(SkillOfflineBlockedError) as blocked:
        repo.commit(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner-1",
            expected_version_id=identity.latest_version_id,
            target_version=3,
            new_locator=f"draft://{_UUID}/v3/{_REV}",
            new_description="published",
            env="test",
        )

    assert blocked.value.impact.counts == {"SERVICE_ARTIFACT": 1}
    with db.orm_session() as session:
        skill = session.query(Skill).filter_by(id=skill_id).one()
        assert skill.offline_at is None
        assert skill.draft_status is None
