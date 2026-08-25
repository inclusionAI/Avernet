"""Persistence implementation for additive Space Skill facts."""

from __future__ import annotations

from uuid import uuid4

from injector import inject
from sqlalchemy import and_, func, or_

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import (
    SkillGrant,
    SkillSpaceBinding,
)
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel, SpaceModel
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillRepository as SpaceSkillRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SpaceCreateData,
    SpaceRecord,
    SpaceSkillCreateData,
    SpaceSkillCreationRecord,
    SpaceSkillGrantRecord,
    SpaceSkillIdentityRecord,
    SpaceSkillOwnerGrantData,
    SpaceSkillOwnershipData,
    SpaceSkillOwnershipRecord,
    SpaceSkillQueryRecord,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class SpaceSkillRepository(SpaceSkillRepositoryProtocol):
    """Small public seam for the additive Space table.

    No existing Legacy service injects this repository.  That keeps feature-off
    traffic on its established Local/Repo/Bot-local paths while giving later
    Space Skill slices one tenant-scoped persistence entry point.
    """

    @inject
    def __init__(self, db: DatabasePlugin):
        self._db = db

    def create_space(self, data: SpaceCreateData) -> SpaceRecord:
        with self._db.orm_session() as session:
            payload = {**data, "updated_by": data["created_by"]}
            space = SpaceModel(**payload)
            session.add(space)
            session.flush()
            session.refresh(space)
            return self._space_to_dict(space)

    def get_space(self, space_id: int, *, env: str) -> SpaceRecord | None:
        with self._db.orm_session() as session:
            space = (
                session.query(SpaceModel)
                .filter(
                    SpaceModel.id == space_id,
                    SpaceModel.env == env,
                    SpaceModel.deleted_at.is_(None),
                )
                .first()
            )
            return self._space_to_dict(space) if space else None

    def create_space_skill(
        self,
        *,
        skill_data: SpaceSkillCreateData,
        ownership_data: SpaceSkillOwnershipData,
        owner_grant_data: SpaceSkillOwnerGrantData,
    ) -> SpaceSkillCreationRecord:
        """Commit a new Space Skill's inseparable initial persistence facts.

        The caller supplies command fields, while this repository supplies the
        server-generated UUID and the initial Draft/Owner invariants. The
        ownership Space and Owner membership are checked in the same current
        tenant/env transaction to reject orphan or cross-tenant facts.
        """
        env = skill_data["env"]
        if ownership_data["env"] != env or owner_grant_data["env"] != env:
            raise ValueError("Space Skill facts must share one env")

        with self._db.orm_session() as session:
            space = (
                session.query(SpaceModel)
                .filter(
                    SpaceModel.id == ownership_data["space_id"],
                    SpaceModel.env == env,
                    SpaceModel.deleted_at.is_(None),
                )
                .one_or_none()
            )
            if space is None:
                raise ValueError("Space Skill ownership requires an active Space")

            member = (
                session.query(SpaceMemberModel)
                .filter(
                    SpaceMemberModel.space_id == space.id,
                    SpaceMemberModel.user_id == owner_grant_data["user_id"],
                    SpaceMemberModel.status == "ACTIVE",
                    SpaceMemberModel.env == env,
                )
                .one_or_none()
            )
            if member is None:
                raise ValueError("Space Skill Owner must be an active Space Member")

            skill_payload = dict(skill_data)
            skill_payload.update(
                skill_uuid=str(uuid4()),
                draft_target_version=1,
                draft_status="EDITING",
            )
            skill = Skill(**skill_payload)
            session.add(skill)
            session.flush()

            ownership = SkillSpaceBinding(**{**ownership_data, "skill_id": skill.id})
            owner_grant_payload = dict(owner_grant_data)
            owner_grant_payload.update(
                skill_id=skill.id,
                role="OWNER",
                status="ACTIVE",
                owner_slot=1,
            )
            owner_grant = SkillGrant(**owner_grant_payload)
            session.add_all((ownership, owner_grant))
            session.flush()
            return {
                "skill": self._skill_to_dict(skill),
                "ownership": self._ownership_to_dict(ownership),
                "owner_grant": self._grant_to_dict(owner_grant),
            }

    def list_space_skills(
        self,
        *,
        space_id: int,
        actor_id: str,
        env: str,
        keyword: str | None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[SpaceSkillQueryRecord]]:
        with self._db.orm_session() as session:
            query = (
                session.query(Skill, SpaceModel.space_type, SkillGrant.role)
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
                .outerjoin(
                    SkillGrant,
                    and_(
                        SkillGrant.skill_id == Skill.id,
                        SkillGrant.user_id == actor_id,
                        SkillGrant.status == "ACTIVE",
                        SkillGrant.env == env,
                    ),
                )
                .filter(
                    SkillSpaceBinding.space_id == space_id,
                    SkillSpaceBinding.env == env,
                    Skill.env == env,
                    SpaceModel.deleted_at.is_(None),
                    Skill.retired_at.is_(None),
                )
            )
            if keyword is not None:
                pattern = f"%{keyword.lower()}%"
                query = query.filter(
                    or_(
                        func.lower(Skill.name).like(pattern),
                        func.lower(Skill.description).like(pattern),
                    )
                )

            total = query.count()
            rows = (
                query.order_by(Skill.gmt_modified.desc(), Skill.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return total, [self._skill_query_to_dict(*row) for row in rows]

    @staticmethod
    def _space_to_dict(space: SpaceModel) -> SpaceRecord:
        return {
            "id": space.id,
            "space_code": space.space_code,
            "space_type": space.space_type,
            "name": space.name,
            "description": space.description,
            "personal_owner_id": space.personal_owner_id,
            "sc_team_id": space.sc_team_id,
            "sc_mapping_status": space.sc_mapping_status,
            "created_by": space.created_by,
            "deleted_at": space.deleted_at,
            "deleted_by": space.deleted_by,
            "env": space.env,
        }

    @staticmethod
    def _skill_to_dict(skill: Skill) -> SpaceSkillIdentityRecord:
        return {
            "id": skill.id,
            "skill_uuid": skill.skill_uuid,
            "draft_target_version": skill.draft_target_version,
            "draft_status": skill.draft_status,
            "env": skill.env,
        }

    @staticmethod
    def _skill_query_to_dict(
        skill: Skill,
        space_type: str,
        current_user_skill_role: str | None,
    ) -> SpaceSkillQueryRecord:
        return {
            "id": skill.id,
            "skill_uuid": skill.skill_uuid,
            "name": skill.name,
            "description": skill.description,
            "status": skill.status,
            "draft_status": skill.draft_status,
            "space_type": space_type,
            "current_user_skill_role": current_user_skill_role,
            "gmt_created": skill.gmt_created,
            "gmt_modified": skill.gmt_modified,
        }

    @staticmethod
    def _ownership_to_dict(
        ownership: SkillSpaceBinding,
    ) -> SpaceSkillOwnershipRecord:
        return {
            "id": ownership.id,
            "skill_id": ownership.skill_id,
            "space_id": ownership.space_id,
            "env": ownership.env,
        }

    @staticmethod
    def _grant_to_dict(grant: SkillGrant) -> SpaceSkillGrantRecord:
        return {
            "id": grant.id,
            "skill_id": grant.skill_id,
            "user_id": grant.user_id,
            "role": grant.role,
            "status": grant.status,
            "owner_slot": grant.owner_slot,
            "env": grant.env,
        }
