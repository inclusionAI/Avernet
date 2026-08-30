"""Transactional persistence for mutable Space Skill Draft facts."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import (
    SkillDraftEditLease,
    SkillGrant,
    SkillSpaceBinding,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillDraftRepository as SpaceSkillDraftRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SpaceSkillDraftRecord,
)
from agentclaw.community.core.skill_center.draft_content import DraftRevisionRef
from agentclaw.community.core.skill_center.errors import (
    DraftEditLeaseTokenRejectedError,
    DraftFrozenError,
    DraftNotFoundError,
    DraftRevisionConflictError,
    SpaceSkillGrantForbiddenError,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class SpaceSkillDraftRepository(SpaceSkillDraftRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def get_draft(
        self, *, space_id: int, skill_id: int, env: str
    ) -> SpaceSkillDraftRecord:
        with self._db.orm_session() as session:
            row = (
                session.query(Skill, SpaceModel.space_type)
                .join(
                    SkillSpaceBinding,
                    (SkillSpaceBinding.skill_id == Skill.id)
                    & (SkillSpaceBinding.env == Skill.env),
                )
                .join(
                    SpaceModel,
                    (SpaceModel.id == SkillSpaceBinding.space_id)
                    & (SpaceModel.env == SkillSpaceBinding.env),
                )
                .filter(
                    Skill.id == skill_id,
                    Skill.env == env,
                    SkillSpaceBinding.space_id == space_id,
                    Skill.draft_status.is_not(None),
                    SpaceModel.deleted_at.is_(None),
                )
                .one_or_none()
            )
            if row is None:
                raise DraftNotFoundError("draft not found")
            return self._record(row[0], row[1])

    def replace_draft_revision(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_revision_id: str,
        fencing_token: int | None,
        new_locator: str,
        new_description: str,
        source_commit_sha: str | None = None,
        env: str,
    ) -> str:
        with self._db.transactional_orm_session() as session:
            row = (
                session.query(Skill, SpaceModel.space_type)
                .join(
                    SkillSpaceBinding,
                    (SkillSpaceBinding.skill_id == Skill.id)
                    & (SkillSpaceBinding.env == Skill.env),
                )
                .join(
                    SpaceModel,
                    (SpaceModel.id == SkillSpaceBinding.space_id)
                    & (SpaceModel.env == SkillSpaceBinding.env),
                )
                .filter(
                    Skill.id == skill_id,
                    Skill.env == env,
                    SkillSpaceBinding.space_id == space_id,
                    SpaceModel.deleted_at.is_(None),
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None or row[0].draft_status is None:
                raise DraftNotFoundError("draft not found")
            skill, space_type = row
            if skill.draft_status == "FROZEN":
                raise DraftFrozenError("draft is frozen")
            current = DraftRevisionRef.from_locator(
                tenant=get_current_avernet_tenant(),
                env=env,
                locator=skill.zip_url,
            )
            if current.revision_id != expected_revision_id:
                raise DraftRevisionConflictError("draft revision changed")
            grant = (
                session.query(SkillGrant.id)
                .filter(
                    SkillGrant.skill_id == skill_id,
                    SkillGrant.user_id == actor_id,
                    SkillGrant.env == env,
                    SkillGrant.status == "ACTIVE",
                    SkillGrant.role.in_(("OWNER", "MANAGER")),
                )
                .one_or_none()
            )
            if grant is None:
                raise SpaceSkillGrantForbiddenError("owner or manager required")
            if space_type == "TEAM":
                lease = (
                    session.query(SkillDraftEditLease)
                    .filter(
                        SkillDraftEditLease.skill_id == skill_id,
                        SkillDraftEditLease.env == env,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if (
                    lease is None
                    or lease.holder_user_id != actor_id
                    or lease.fencing_token != fencing_token
                ):
                    raise DraftEditLeaseTokenRejectedError(
                        "stale draft lease fencing token"
                    )
            skill.zip_url = new_locator
            skill.draft_description = new_description
            if source_commit_sha is not None:
                skill.source_commit_sha = source_commit_sha
            session.flush()
            return current.locator

    @staticmethod
    def _record(skill: Skill, space_type: str) -> SpaceSkillDraftRecord:
        return {
            "skill_id": skill.id,
            "skill_uuid": skill.skill_uuid,
            "name": skill.name,
            "draft_description": skill.draft_description,
            "target_version": skill.draft_target_version,
            "status": skill.draft_status,
            "locator": skill.zip_url,
            "source_kind": skill.draft_source_kind,
            "source_repo_url": skill.source_repo_url,
            "source_branch": skill.source_branch,
            "source_subdir": skill.source_subdir,
            "source_commit_sha": skill.source_commit_sha,
            "space_type": space_type,
        }
