"""Read-model tests for final Space Skill workshop facts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import (
    SkillDraftEditLease,
    SkillGrant,
    SkillPublicationAttempt,
    SkillSpaceBinding,
    SkillVersion,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_read import (
    SpaceSkillReadRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_version_read import (
    SpaceSkillVersionReadRepository,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.work_orders.repository.models import WorkOrderModel


class _Database:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    @contextmanager
    def orm_session(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()


def test_read_model_projects_independent_draft_version_attempt_and_actor_facts():
    db = _Database()
    timestamp = datetime(2026, 8, 30, 8)
    with db.orm_session() as session:
        space = SpaceModel(
            space_code="team-read",
            space_type="TEAM",
            name="Read Team",
            created_by="owner-1",
            updated_by="owner-1",
            env="test",
        )
        session.add(space)
        session.flush()
        session.add_all(
            [
                SpaceMemberModel(
                    space_id=space.id,
                    user_id="owner-1",
                    user_name="Owner One",
                    role="MEMBER",
                    created_by="owner-1",
                    env="test",
                ),
                SpaceMemberModel(
                    space_id=space.id,
                    user_id="viewer-1",
                    user_name="Viewer One",
                    role="MEMBER",
                    created_by="owner-1",
                    env="test",
                ),
            ]
        )
        skill = Skill(
            name="risk-review",
            description="Published description",
            env="test",
            skill_uuid="11111111-1111-4111-8111-111111111111",
            source_type="GIT",
            source_repo_url="https://example.com/skills.git",
            source_branch="main",
            source_subdir="risk-review",
            source_commit_sha="a" * 40,
            zip_url=(
                "draft://11111111-1111-4111-8111-111111111111/"
                "v2/22222222-2222-4222-8222-222222222222"
            ),
            draft_target_version=2,
            draft_status="FROZEN",
            draft_description="Draft description",
            draft_source_kind="GIT",
            gmt_created=timestamp,
            gmt_modified=timestamp,
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
                SkillDraftEditLease(
                    skill_id=skill.id,
                    holder_user_id="owner-1",
                    fencing_token=3,
                    env="test",
                ),
                SkillVersion(
                    skill_id=skill.id,
                    version_ordinal=1,
                    status="PUBLISHED",
                    sc_version_number="1.0.0",
                    name="risk-review",
                    description="Published description",
                    published_at=timestamp,
                    created_by="owner-1",
                    env="test",
                ),
                SkillVersion(
                    skill_id=skill.id,
                    version_ordinal=2,
                    status="MATERIALIZING",
                    sc_version_number="2.0.0",
                    name="risk-review",
                    description="Not ready",
                    created_by="owner-1",
                    env="test",
                ),
                SkillPublicationAttempt(
                    skill_id=skill.id,
                    request_id="publish-2",
                    active_skill_key=f"test:{skill.id}",
                    target_version_ordinal=2,
                    status="MATERIALIZING",
                    created_by="owner-1",
                    env="test",
                ),
                WorkOrderModel(
                    work_order_no="WO-1",
                    biz_type="SKILL_COLLABORATOR",
                    biz_id=str(skill.id),
                    applicant_user_id="viewer-1",
                    status="PENDING",
                    env="test",
                ),
            ]
        )
        skill_id = skill.id
        space_id = space.id

    total, records = SpaceSkillReadRepository(db).list_skills(
        space_id=space_id,
        actor_id="viewer-1",
        env="test",
        keyword="PUBLISHED",
        offset=0,
        limit=20,
    )

    assert total == 1
    record = records[0]
    assert record["id"] == skill_id
    assert record["owner_user_id"] == "owner-1"
    assert record["owner_display_name"] == "Owner One"
    assert record["latest_version_ordinal"] == 1
    assert record["draft_target_version"] == 2
    assert record["active_attempt_status"] == "MATERIALIZING"
    assert record["pending_request_no"] == "WO-1"

    versions = SpaceSkillVersionReadRepository(db)
    version_total, published = versions.list_published(
        space_id=space_id, skill_id=skill_id, env="test", offset=0, limit=20
    )
    assert version_total == 1
    assert [row["version_ordinal"] for row in published] == [1]
    assert [row["skill_id"] for row in versions.list_consumable_candidates(
        space_id=space_id, env="test", keyword=None
    )] == [skill_id]

    with db.orm_session() as session:
        session.query(Skill).filter(Skill.id == skill_id).one().offline_at = timestamp
    assert versions.list_consumable_candidates(
        space_id=space_id, env="test", keyword=None
    ) == []
