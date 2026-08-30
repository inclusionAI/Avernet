"""Transaction tests for Space Skill Draft revision CAS and fencing."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import (
    SkillDraftEditLease,
    SkillDraftUpgradeRequest,
    SkillGrant,
    SkillSpaceBinding,
    SkillVersion,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_draft import (
    SpaceSkillDraftRepository,
)
from agentclaw.community.core.skill_center.errors import (
    DraftEditLeaseTokenRejectedError,
    DraftFrozenError,
    DraftRevisionConflictError,
    SpaceSkillIdempotencyConflictError,
    SpaceSkillGrantForbiddenError,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel


_UUID = "11111111-1111-4111-8111-111111111111"
_OLD_REV = "22222222-2222-4222-8222-222222222222"
_NEW_REV = "33333333-3333-4333-8333-333333333333"


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


class _UniqueRaceDatabase:
    """Expose a committed winner after the losing insert hits its unique key."""

    def __init__(self, winner: _Database) -> None:
        self._winner = winner

    orm_session = property(lambda self: self._winner.orm_session)

    @contextmanager
    def transactional_orm_session(self):
        raise IntegrityError("INSERT", {}, RuntimeError("duplicate request key"))
        yield  # pragma: no cover - contextmanager shape only


def _seed(db: _Database, *, space_type: str, frozen: bool = False):
    with db.orm_session() as session:
        space = SpaceModel(
            space_code=f"{space_type.lower()}-draft",
            space_type=space_type,
            name="Draft Space",
            created_by="owner-1",
            updated_by="owner-1",
            personal_owner_id="owner-1" if space_type == "PERSONAL" else None,
            env="test",
        )
        session.add(space)
        session.flush()
        skill = Skill(
            name="draft-skill",
            description=None,
            env="test",
            skill_uuid=_UUID,
            zip_url=f"draft://{_UUID}/v1/{_OLD_REV}",
            draft_target_version=1,
            draft_status="FROZEN" if frozen else "EDITING",
            draft_description="old",
            draft_source_kind="FOLDER",
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
            ]
        )
        if space_type == "TEAM":
            session.add(
                SkillDraftEditLease(
                    skill_id=skill.id,
                    holder_user_id="owner-1",
                    fencing_token=7,
                    env="test",
                )
            )
        return space.id, skill.id


def _replace(repo, *, space_id, skill_id, expected=_OLD_REV, token=None):
    return repo.replace_draft_revision(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        expected_revision_id=expected,
        fencing_token=token,
        new_locator=f"draft://{_UUID}/v1/{_NEW_REV}",
        new_description="new",
        env="test",
    )


def test_personal_draft_uses_revision_cas_without_lease():
    db = _Database()
    space_id, skill_id = _seed(db, space_type="PERSONAL")
    repo = SpaceSkillDraftRepository(db)

    assert _replace(repo, space_id=space_id, skill_id=skill_id) == (
        f"draft://{_UUID}/v1/{_OLD_REV}"
    )
    with pytest.raises(DraftRevisionConflictError):
        _replace(repo, space_id=space_id, skill_id=skill_id)


def test_team_draft_requires_current_holder_fencing_token():
    db = _Database()
    space_id, skill_id = _seed(db, space_type="TEAM")
    repo = SpaceSkillDraftRepository(db)

    with pytest.raises(DraftEditLeaseTokenRejectedError):
        _replace(repo, space_id=space_id, skill_id=skill_id, token=6)

    assert _replace(repo, space_id=space_id, skill_id=skill_id, token=7).endswith(
        _OLD_REV
    )


def test_mutation_preflight_requires_editor_grant_before_external_io():
    db = _Database()
    space_id, skill_id = _seed(db, space_type="TEAM")
    repo = SpaceSkillDraftRepository(db)

    with pytest.raises(SpaceSkillGrantForbiddenError):
        repo.get_draft_for_mutation(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="member-1",
            expected_revision_id=_OLD_REV,
            fencing_token=7,
            env="test",
        )

    record = repo.get_draft_for_mutation(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        expected_revision_id=_OLD_REV,
        fencing_token=7,
        env="test",
    )
    assert record["skill_id"] == skill_id


def test_frozen_draft_rejects_revision_mutation():
    db = _Database()
    space_id, skill_id = _seed(db, space_type="PERSONAL", frozen=True)

    with pytest.raises(DraftFrozenError):
        _replace(SpaceSkillDraftRepository(db), space_id=space_id, skill_id=skill_id)


def test_team_delete_requires_a_held_lease_and_exact_fencing_token():
    db = _Database()
    space_id, skill_id = _seed(db, space_type="TEAM")
    with db.orm_session() as session:
        lease = session.query(SkillDraftEditLease).filter_by(skill_id=skill_id).one()
        lease.holder_user_id = None

    with pytest.raises(DraftEditLeaseTokenRejectedError):
        SpaceSkillDraftRepository(db).delete_draft(
            space_id=space_id,
            skill_id=skill_id,
            actor_id="owner-1",
            expected_revision_id=_OLD_REV,
            fencing_token=None,
            env="test",
        )


def test_delete_first_draft_removes_the_whole_unreferenced_skill_aggregate():
    db = _Database()
    space_id, skill_id = _seed(db, space_type="PERSONAL")

    result = SpaceSkillDraftRepository(db).delete_draft(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        expected_revision_id=_OLD_REV,
        fencing_token=None,
        env="test",
    )

    assert result["deleted_scope"] == "SKILL"
    with db.orm_session() as session:
        assert session.query(Skill).filter_by(id=skill_id).one_or_none() is None
        assert session.query(SkillGrant).filter_by(skill_id=skill_id).count() == 0
        assert (
            session.query(SkillSpaceBinding).filter_by(skill_id=skill_id).count() == 0
        )


def test_delete_upgrade_draft_preserves_published_skill_history():
    db = _Database()
    space_id, skill_id = _seed(db, space_type="PERSONAL")
    with db.orm_session() as session:
        session.add(
            SkillVersion(
                skill_id=skill_id,
                version_ordinal=1,
                status="PUBLISHED",
                sc_version_number="1.0.0",
                name="draft-skill",
                description="published",
                created_by="owner-1",
                env="test",
            )
        )

    result = SpaceSkillDraftRepository(db).delete_draft(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        expected_revision_id=_OLD_REV,
        fencing_token=None,
        env="test",
    )

    assert result["deleted_scope"] == "DRAFT"
    with db.orm_session() as session:
        skill = session.query(Skill).filter_by(id=skill_id).one()
        assert skill.draft_status is None
        assert skill.zip_url is None
        assert session.query(SkillVersion).filter_by(skill_id=skill_id).count() == 1


def test_delete_upgrade_draft_preserves_spent_idempotency_request():
    db = _Database()
    space_id, skill_id = _seed(db, space_type="PERSONAL")
    with db.orm_session() as session:
        skill = session.query(Skill).filter_by(id=skill_id).one()
        skill.zip_url = None
        skill.draft_target_version = None
        skill.draft_status = None
        skill.draft_description = None
        skill.draft_source_kind = None
        version = SkillVersion(
            skill_id=skill_id,
            version_ordinal=1,
            status="PUBLISHED",
            sc_version_number="1.0.0",
            name="draft-skill",
            description="published",
            created_by="owner-1",
            env="test",
        )
        session.add(version)
        session.flush()
        version_id = version.id

    repo = SpaceSkillDraftRepository(db)
    created = repo.create_upgrade_draft(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        request_id="upgrade-then-delete",
        expected_version_id=version_id,
        target_version=2,
        new_locator=f"draft://{_UUID}/v2/{_NEW_REV}",
        new_description="upgrade",
        env="test",
    )
    assert created["created"] is True
    active = repo.get_upgrade_by_request_id(
        request_id="upgrade-then-delete", env="test"
    )
    assert active is not None
    assert active["status"] == "ACTIVE"
    assert active["draft"] is not None
    assert active["draft"]["locator"].endswith(_NEW_REV)

    replayed = repo.create_upgrade_draft(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        request_id="upgrade-then-delete",
        expected_version_id=version_id,
        target_version=2,
        new_locator=f"draft://{_UUID}/v2/unused",
        new_description="ignored replay",
        env="test",
    )
    assert replayed["created"] is False
    assert replayed["draft"]["locator"].endswith(_NEW_REV)

    repo.delete_draft(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        expected_revision_id=_NEW_REV,
        fencing_token=None,
        env="test",
    )

    assert repo.get_upgrade_by_request_id(
        request_id="upgrade-then-delete", env="test"
    ) == {
        "skill_id": skill_id,
        "space_id": space_id,
        "status": "SPENT",
        "draft": None,
    }


def test_upgrade_unique_race_reloads_winner_and_rejects_cross_skill_reuse():
    db = _Database()
    space_id, skill_id = _seed(db, space_type="PERSONAL")
    with db.orm_session() as session:
        session.add(
            SkillDraftUpgradeRequest(
                skill_id=skill_id,
                space_id=space_id,
                request_id="upgrade-race",
                target_version_ordinal=1,
                status="ACTIVE",
                created_by="owner-1",
                env="test",
            )
        )

    repo = SpaceSkillDraftRepository(_UniqueRaceDatabase(db))
    replayed = repo.create_upgrade_draft(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner-1",
        request_id="upgrade-race",
        expected_version_id=1,
        target_version=1,
        new_locator="unused",
        new_description="unused",
        env="test",
    )

    assert replayed["created"] is False
    assert replayed["draft"]["locator"].endswith(_OLD_REV)
    with pytest.raises(SpaceSkillIdempotencyConflictError):
        repo.create_upgrade_draft(
            space_id=space_id,
            skill_id=skill_id + 1,
            actor_id="owner-1",
            request_id="upgrade-race",
            expected_version_id=1,
            target_version=1,
            new_locator="unused",
            new_description="unused",
            env="test",
        )
