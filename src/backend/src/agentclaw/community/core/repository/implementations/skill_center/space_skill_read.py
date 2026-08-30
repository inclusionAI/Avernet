"""Stable workshop read model for Space-owned Skills."""

from __future__ import annotations

from injector import inject
from sqlalchemy import String, and_, func, or_
from sqlalchemy.orm import aliased

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import (
    SkillDraftEditLease,
    SkillGrant,
    SkillPublicationAttempt,
    SkillSpaceBinding,
    SkillVersion,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillReadRepository as SpaceSkillReadRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SpaceSkillReadRecord,
)
from agentclaw.community.core.skill_center.errors import DraftNotFoundError
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderBizType,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import WorkOrderModel
from agentclaw.community.plugin_api.database import DatabasePlugin


_ACTIVE_ATTEMPT_STATES = (
    "PREPARING",
    "SC_SUBMITTING",
    "WAITING_SC",
    "MATERIALIZING",
    "RESULT_UNKNOWN",
)


class SpaceSkillReadRepository(SpaceSkillReadRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def list_skills(
        self,
        *,
        space_id: int,
        actor_id: str,
        env: str,
        keyword: str | None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[SpaceSkillReadRecord]]:
        with self._db.orm_session() as session:
            query, columns = self._query(session, actor_id=actor_id, env=env)
            query = query.filter(SkillSpaceBinding.space_id == space_id)
            if keyword is not None:
                pattern = f"%{keyword.lower()}%"
                query = query.filter(
                    or_(
                        func.lower(Skill.name).like(pattern),
                        func.lower(
                            func.coalesce(Skill.description, Skill.draft_description)
                        ).like(pattern),
                    )
                )
            total = query.count()
            rows = (
                query.order_by(Skill.gmt_modified.desc(), Skill.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return total, [self._record(row, columns) for row in rows]

    def get_skill(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> SpaceSkillReadRecord:
        with self._db.orm_session() as session:
            query, columns = self._query(session, actor_id=actor_id, env=env)
            row = query.filter(
                SkillSpaceBinding.space_id == space_id,
                Skill.id == skill_id,
            ).one_or_none()
            if row is None:
                raise DraftNotFoundError("space skill not found")
            return self._record(row, columns)

    @staticmethod
    def _query(session, *, actor_id: str, env: str):
        owner_member = aliased(SpaceMemberModel)
        holder_member = aliased(SpaceMemberModel)
        actor_grant = (
            session.query(
                SkillGrant.skill_id.label("skill_id"),
                SkillGrant.role.label("role"),
            )
            .filter(
                SkillGrant.env == env,
                SkillGrant.user_id == actor_id,
                SkillGrant.status == "ACTIVE",
            )
            .subquery()
        )
        latest = (
            session.query(
                SkillVersion.skill_id.label("skill_id"),
                func.max(SkillVersion.version_ordinal).label("version_ordinal"),
            )
            .filter(SkillVersion.env == env, SkillVersion.status == "PUBLISHED")
            .group_by(SkillVersion.skill_id)
            .subquery()
        )
        columns = {
            "space_type": SpaceModel.space_type,
            "actor_role": actor_grant.c.role,
            "owner_user_id": SkillGrant.user_id,
            "owner_display_name": owner_member.user_name,
            "lease_holder_user_id": SkillDraftEditLease.holder_user_id,
            "lease_holder_display_name": holder_member.user_name,
            "latest_version_id": SkillVersion.id,
            "latest_version_ordinal": SkillVersion.version_ordinal,
            "latest_sc_version_number": SkillVersion.sc_version_number,
            "latest_published_at": SkillVersion.published_at,
            "active_attempt_id": SkillPublicationAttempt.id,
            "active_attempt_target_version": SkillPublicationAttempt.target_version_ordinal,
            "active_attempt_status": SkillPublicationAttempt.status,
            "pending_request_id": WorkOrderModel.id,
            "pending_request_no": WorkOrderModel.work_order_no,
        }
        query = (
            session.query(Skill, *columns.values())
            .join(
                SkillSpaceBinding,
                and_(
                    SkillSpaceBinding.skill_id == Skill.id,
                    SkillSpaceBinding.env == Skill.env,
                ),
            )
            .join(
                SpaceModel,
                and_(
                    SpaceModel.id == SkillSpaceBinding.space_id,
                    SpaceModel.env == SkillSpaceBinding.env,
                ),
            )
            .join(
                SkillGrant,
                and_(
                    SkillGrant.skill_id == Skill.id,
                    SkillGrant.env == env,
                    SkillGrant.status == "ACTIVE",
                    SkillGrant.role == "OWNER",
                    SkillGrant.owner_slot == 1,
                ),
            )
            .outerjoin(
                actor_grant,
                actor_grant.c.skill_id == Skill.id,
            )
            .outerjoin(
                owner_member,
                and_(
                    owner_member.space_id == SkillSpaceBinding.space_id,
                    owner_member.user_id == SkillGrant.user_id,
                    owner_member.env == env,
                ),
            )
            .outerjoin(
                SkillDraftEditLease,
                and_(
                    SkillDraftEditLease.skill_id == Skill.id,
                    SkillDraftEditLease.env == env,
                ),
            )
            .outerjoin(
                holder_member,
                and_(
                    holder_member.space_id == SkillSpaceBinding.space_id,
                    holder_member.user_id == SkillDraftEditLease.holder_user_id,
                    holder_member.env == env,
                ),
            )
            .outerjoin(latest, latest.c.skill_id == Skill.id)
            .outerjoin(
                SkillVersion,
                and_(
                    SkillVersion.skill_id == latest.c.skill_id,
                    SkillVersion.version_ordinal == latest.c.version_ordinal,
                    SkillVersion.env == env,
                    SkillVersion.status == "PUBLISHED",
                ),
            )
            .outerjoin(
                SkillPublicationAttempt,
                and_(
                    SkillPublicationAttempt.skill_id == Skill.id,
                    SkillPublicationAttempt.env == env,
                    SkillPublicationAttempt.status.in_(_ACTIVE_ATTEMPT_STATES),
                ),
            )
            .outerjoin(
                WorkOrderModel,
                and_(
                    WorkOrderModel.biz_type
                    == WorkOrderBizType.SKILL_COLLABORATOR.value,
                    WorkOrderModel.biz_id == func.cast(Skill.id, String),
                    WorkOrderModel.applicant_user_id == actor_id,
                    WorkOrderModel.status == WorkOrderStatus.PENDING.value,
                    WorkOrderModel.env == env,
                ),
            )
            .filter(
                Skill.env == env,
                SkillSpaceBinding.env == env,
                SpaceModel.deleted_at.is_(None),
            )
        )
        return query, columns

    @staticmethod
    def _record(row, columns) -> SpaceSkillReadRecord:
        skill = row[0]
        values = dict(zip(columns, row[1:], strict=True))
        return {
            "id": skill.id,
            "skill_uuid": skill.skill_uuid,
            "name": skill.name,
            "description": skill.description,
            "status": skill.status,
            "draft_status": skill.draft_status,
            "source_type": skill.source_type,
            "draft_target_version": skill.draft_target_version,
            "draft_description": skill.draft_description,
            "draft_locator": skill.zip_url,
            "draft_source_kind": skill.draft_source_kind,
            "source_repo_url": skill.source_repo_url,
            "source_branch": skill.source_branch,
            "source_subdir": skill.source_subdir,
            "source_commit_sha": skill.source_commit_sha,
            "offline_at": skill.offline_at,
            "offline_by": skill.offline_by,
            "space_type": values["space_type"],
            "current_user_skill_role": values["actor_role"],
            "owner_user_id": values["owner_user_id"],
            "owner_display_name": values["owner_display_name"],
            "lease_holder_user_id": values["lease_holder_user_id"],
            "lease_holder_display_name": values["lease_holder_display_name"],
            "latest_version_id": values["latest_version_id"],
            "latest_version_ordinal": values["latest_version_ordinal"],
            "latest_sc_version_number": values["latest_sc_version_number"],
            "latest_published_at": values["latest_published_at"],
            "active_attempt_id": values["active_attempt_id"],
            "active_attempt_target_version": values[
                "active_attempt_target_version"
            ],
            "active_attempt_status": values["active_attempt_status"],
            "pending_request_id": values["pending_request_id"],
            "pending_request_no": values["pending_request_no"],
            "gmt_created": skill.gmt_created,
            "gmt_modified": skill.gmt_modified,
        }
