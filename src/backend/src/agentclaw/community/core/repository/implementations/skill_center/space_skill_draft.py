"""Transactional persistence for mutable Space Skill Draft facts."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSetSkill,
)
from agentclaw.community.core.models.space_skill import (
    SkillDraftEditLease,
    SkillGrant,
    SkillSpaceBinding,
    SkillPublicationAttempt,
    SkillVersion,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillDraftRepository as SpaceSkillDraftRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SpaceSkillDraftRecord,
    DraftDeleteRecord,
    DraftUpgradeRecord,
    SkillUpgradeIdentityRecord,
)
from agentclaw.community.core.skill_center.draft_content import DraftRevisionRef
from agentclaw.community.core.skill_center.errors import (
    DraftEditLeaseTokenRejectedError,
    DraftEditLeaseConflictError,
    DraftFrozenError,
    DraftAlreadyExistsError,
    DraftNotFoundError,
    DraftRevisionConflictError,
    SpaceSkillGrantForbiddenError,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.core.work_orders.repository.models import WorkOrderModel
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

    def get_skill_for_upgrade(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> SkillUpgradeIdentityRecord:
        with self._db.orm_session() as session:
            row = (
                session.query(Skill, SpaceModel)
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
                .join(
                    SkillGrant,
                    (SkillGrant.skill_id == Skill.id)
                    & (SkillGrant.env == env)
                    & (SkillGrant.user_id == actor_id)
                    & (SkillGrant.status == "ACTIVE")
                    & (SkillGrant.role.in_(("OWNER", "MANAGER"))),
                )
                .filter(
                    Skill.id == skill_id,
                    Skill.env == env,
                    SkillSpaceBinding.space_id == space_id,
                )
                .one_or_none()
            )
            if row is None:
                raise SpaceSkillGrantForbiddenError("owner or manager required")
            return {
                "skill_id": row[0].id,
                "skill_uuid": row[0].skill_uuid,
                "name": row[0].name,
                "space_type": row[1].space_type,
                "sc_team_id": row[1].sc_team_id,
            }

    def get_upgrade_by_request_id(
        self, *, request_id: str, env: str
    ) -> SpaceSkillDraftRecord | None:
        with self._db.orm_session() as session:
            row = (
                session.query(Skill, SpaceModel.space_type, SpaceModel.sc_team_id)
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
                .filter(Skill.draft_request_id == request_id, Skill.env == env)
                .one_or_none()
            )
            return self._record(row[0], row[1], row[2]) if row is not None else None

    def create_upgrade_draft(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        request_id: str,
        expected_version_id: int,
        target_version: int,
        new_locator: str,
        new_description: str,
        env: str,
    ) -> DraftUpgradeRecord:
        with self._db.transactional_orm_session() as session:
            row = (
                session.query(Skill, SpaceModel)
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
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise DraftNotFoundError("space skill not found")
            skill, space = row
            grant = session.query(SkillGrant.id).filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == actor_id,
                SkillGrant.env == env,
                SkillGrant.status == "ACTIVE",
                SkillGrant.role.in_(("OWNER", "MANAGER")),
            ).one_or_none()
            if grant is None:
                raise SpaceSkillGrantForbiddenError("owner or manager required")
            if skill.draft_status is not None:
                if skill.draft_request_id == request_id:
                    return {"created": False, "draft": self._record(skill, space.space_type, space.sc_team_id)}
                raise DraftAlreadyExistsError("draft already exists")
            latest = (
                session.query(SkillVersion)
                .filter(
                    SkillVersion.skill_id == skill_id,
                    SkillVersion.env == env,
                    SkillVersion.status == "PUBLISHED",
                )
                .order_by(SkillVersion.version_ordinal.desc())
                .with_for_update()
                .first()
            )
            if latest is None or latest.id != expected_version_id:
                raise DraftRevisionConflictError("latest Published Version changed")
            skill.zip_url = new_locator
            skill.draft_target_version = target_version
            skill.draft_status = "EDITING"
            skill.draft_description = new_description
            skill.draft_source_kind = "PUBLISHED_VERSION"
            skill.draft_request_id = request_id
            session.flush()
            return {"created": True, "draft": self._record(skill, space.space_type, space.sc_team_id)}

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

    def delete_draft(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_revision_id: str,
        fencing_token: int | None,
        env: str,
    ) -> DraftDeleteRecord:
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
                tenant=get_current_avernet_tenant(), env=env, locator=skill.zip_url
            )
            if current.revision_id != expected_revision_id:
                raise DraftRevisionConflictError("draft revision changed")
            grant = session.query(SkillGrant.id).filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == actor_id,
                SkillGrant.env == env,
                SkillGrant.status == "ACTIVE",
                SkillGrant.role.in_(("OWNER", "MANAGER")),
            ).one_or_none()
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
                if lease is not None and lease.holder_user_id not in {None, actor_id}:
                    raise DraftEditLeaseConflictError("draft lease is held by another actor")
                if (
                    lease is not None
                    and lease.holder_user_id == actor_id
                    and lease.fencing_token != fencing_token
                ):
                    raise DraftEditLeaseTokenRejectedError("stale fencing token")
            external = any(
                session.query(model).filter(
                    getattr(model, "skill_id", None) == skill_id,
                    model.env == env,
                ).first()
                is not None
                for model in (
                    SkillVersion,
                    SkillPublicationAttempt,
                    SkillSetSkill,
                    BotSkillInstallation,
                )
            ) or session.query(WorkOrderModel.id).filter(
                WorkOrderModel.biz_type == "SKILL_COLLABORATOR",
                WorkOrderModel.biz_id == str(skill_id),
                WorkOrderModel.env == env,
            ).first() is not None
            if external:
                skill.zip_url = None
                skill.draft_target_version = None
                skill.draft_status = None
                skill.draft_description = None
                skill.draft_source_kind = None
                skill.draft_request_id = None
                scope = "DRAFT"
            else:
                session.query(SkillDraftEditLease).filter_by(
                    skill_id=skill_id, env=env
                ).delete(synchronize_session=False)
                session.query(SkillGrant).filter_by(
                    skill_id=skill_id, env=env
                ).delete(synchronize_session=False)
                session.query(SkillSpaceBinding).filter_by(
                    skill_id=skill_id, env=env
                ).delete(synchronize_session=False)
                session.delete(skill)
                scope = "SKILL"
            session.flush()
            return {"changed": True, "deleted_scope": scope, "locator": current.locator}

    @staticmethod
    def _record(
        skill: Skill, space_type: str, sc_team_id: int | None = None
    ) -> SpaceSkillDraftRecord:
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
            "sc_team_id": sc_team_id,
        }
