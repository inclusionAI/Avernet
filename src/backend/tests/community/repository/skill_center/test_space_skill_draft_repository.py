"""Transaction tests for Space Skill Draft revision CAS and fencing."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import (
    SkillDraftEditLease,
    SkillGrant,
    SkillSpaceBinding,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_draft import (
    SpaceSkillDraftRepository,
)
from agentclaw.community.core.skill_center.errors import (
    DraftEditLeaseTokenRejectedError,
    DraftFrozenError,
    DraftRevisionConflictError,
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


def test_frozen_draft_rejects_revision_mutation():
    db = _Database()
    space_id, skill_id = _seed(db, space_type="PERSONAL", frozen=True)

    with pytest.raises(DraftFrozenError):
        _replace(
            SpaceSkillDraftRepository(db), space_id=space_id, skill_id=skill_id
        )
