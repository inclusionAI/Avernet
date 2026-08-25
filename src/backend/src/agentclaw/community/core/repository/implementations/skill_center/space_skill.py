"""Persistence implementation for additive Space Skill facts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from injector import inject
from sqlalchemy import and_, func, or_

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import (
    SkillGrant,
    SkillSpaceBinding,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillEditorRequestRepositoryProtocol,
    SpaceSkillRepository as SpaceSkillRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SpaceCreateData,
    SpaceRecord,
    SpaceSkillCreateData,
    SpaceSkillCreationRecord,
    SpaceSkillGrantRecord,
    SpaceSkillGrantItem,
    SpaceSkillGrantSetRecord,
    SpaceSkillIdentityRecord,
    SpaceSkillOwnerGrantData,
    SpaceSkillOwnershipData,
    SpaceSkillOwnershipRecord,
    SpaceSkillQueryRecord,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.skill_center.errors import (
    SpaceSkillGrantConflictError,
    SpaceSkillGrantForbiddenError,
    SpaceSkillGrantMemberRequiredError,
    SpaceSkillGrantNotFoundError,
    SpaceSkillGrantReasonRequiredError,
)


class SpaceSkillRepository(SpaceSkillRepositoryProtocol):
    """Small public seam for the additive Space table.

    No existing Legacy service injects this repository.  That keeps feature-off
    traffic on its established Local/Repo/Bot-local paths while giving later
    Space Skill slices one tenant-scoped persistence entry point.
    """

    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        skill_editor_requests: SkillEditorRequestRepositoryProtocol,
    ):
        self._db = db
        self._skill_editor_requests = skill_editor_requests

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

    def list_grants(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> SpaceSkillGrantSetRecord:
        with self._db.transactional_orm_session() as session:
            self._require_binding(
                session, space_id=space_id, skill_id=skill_id, env=env
            )
            return self._active_grant_set(
                session, skill_id=skill_id, actor_id=actor_id, env=env
            )

    def get_active_role(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> str | None:
        with self._db.orm_session() as session:
            self._require_binding(
                session, space_id=space_id, skill_id=skill_id, env=env
            )
            grant = (
                session.query(SkillGrant)
                .filter(
                    SkillGrant.skill_id == skill_id,
                    SkillGrant.user_id == actor_id,
                    SkillGrant.env == env,
                    SkillGrant.status == "ACTIVE",
                )
                .one_or_none()
            )
            return grant.role if grant is not None else None

    def add_manager(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        manager_user_id: str,
        env: str,
    ) -> SpaceSkillGrantItem:
        with self._db.orm_session() as session:
            self._lock_binding_and_require_owner(
                session,
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env=env,
            )
            self._require_active_member(
                session, space_id=space_id, user_id=manager_user_id, env=env
            )
            grant = (
                session.query(SkillGrant)
                .filter(
                    SkillGrant.skill_id == skill_id,
                    SkillGrant.user_id == manager_user_id,
                    SkillGrant.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if grant is not None and grant.role == "OWNER" and grant.status == "ACTIVE":
                raise SpaceSkillGrantConflictError("owner cannot also be manager")
            if grant is None:
                grant = SkillGrant(
                    skill_id=skill_id,
                    user_id=manager_user_id,
                    role="MANAGER",
                    status="ACTIVE",
                    owner_slot=None,
                    granted_by=actor_id,
                    env=env,
                )
                session.add(grant)
            else:
                grant.role = "MANAGER"
                grant.status = "ACTIVE"
                grant.owner_slot = None
                grant.granted_by = actor_id
                grant.revoked_at = None
                grant.revoked_by = None
            session.flush()
            return {"user_id": grant.user_id, "role": "MANAGER"}

    def remove_manager(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        manager_user_id: str,
        env: str,
    ) -> SpaceSkillGrantItem:
        with self._db.orm_session() as session:
            self._lock_binding_and_require_owner(
                session,
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env=env,
            )
            grant = (
                session.query(SkillGrant)
                .filter(
                    SkillGrant.skill_id == skill_id,
                    SkillGrant.user_id == manager_user_id,
                    SkillGrant.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if grant is not None and grant.role == "OWNER" and grant.status == "ACTIVE":
                raise SpaceSkillGrantConflictError("owner cannot be removed as manager")
            if grant is not None and grant.status == "ACTIVE":
                grant.status = "REVOKED"
                grant.revoked_at = datetime.now(UTC).replace(tzinfo=None)
                grant.revoked_by = actor_id
                session.flush()
            return {"user_id": manager_user_id, "role": "MANAGER"}

    def transfer_owner(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        new_owner_user_id: str,
        reason: str | None,
        env: str,
    ) -> SpaceSkillGrantSetRecord:
        with self._db.transactional_orm_session() as session:
            self._require_binding(
                session, space_id=space_id, skill_id=skill_id, env=env, lock=True
            )
            space = (
                session.query(SpaceModel)
                .filter(
                    SpaceModel.id == space_id,
                    SpaceModel.env == env,
                    SpaceModel.deleted_at.is_(None),
                )
                .with_for_update()
                .one_or_none()
            )
            current_owner = (
                session.query(SkillGrant)
                .filter(
                    SkillGrant.skill_id == skill_id,
                    SkillGrant.env == env,
                    SkillGrant.status == "ACTIVE",
                    SkillGrant.owner_slot == 1,
                )
                .with_for_update()
                .one_or_none()
            )
            if space is None or current_owner is None:
                raise SpaceSkillGrantConflictError("skill has no active owner")
            actor_member = self._active_member(
                session, space_id=space_id, user_id=actor_id, env=env, lock=True
            )
            if actor_member is None:
                raise SpaceSkillGrantForbiddenError("active membership required")
            is_admin = actor_id == space.created_by or (
                actor_member.role in {"ADMIN", "OWNER", "ADMINISTRATOR"}
            )
            if actor_id != current_owner.user_id and not is_admin:
                raise SpaceSkillGrantForbiddenError("owner or space admin required")
            if actor_id != current_owner.user_id and not reason:
                raise SpaceSkillGrantReasonRequiredError("space admin reason required")
            self._require_active_member(
                session, space_id=space_id, user_id=new_owner_user_id, env=env
            )
            if new_owner_user_id == current_owner.user_id:
                return self._active_grant_set(
                    session, skill_id=skill_id, actor_id=actor_id, env=env
                )

            target = (
                session.query(SkillGrant)
                .filter(
                    SkillGrant.skill_id == skill_id,
                    SkillGrant.user_id == new_owner_user_id,
                    SkillGrant.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            current_owner.status = "REVOKED"
            current_owner.owner_slot = None
            current_owner.revoked_at = datetime.now(UTC).replace(tzinfo=None)
            current_owner.revoked_by = actor_id
            session.flush()

            if target is None:
                target = SkillGrant(
                    skill_id=skill_id,
                    user_id=new_owner_user_id,
                    role="OWNER",
                    status="ACTIVE",
                    owner_slot=1,
                    granted_by=actor_id,
                    grant_reason=reason,
                    env=env,
                )
                session.add(target)
            else:
                target.role = "OWNER"
                target.status = "ACTIVE"
                target.owner_slot = 1
                target.granted_by = actor_id
                target.grant_reason = reason
                target.revoked_at = None
                target.revoked_by = None
            session.flush()
            self._skill_editor_requests.reroute_pending_reviewer(
                session,
                skill_id=skill_id,
                previous_owner_user_id=current_owner.user_id,
                new_owner_user_id=new_owner_user_id,
                env=env,
            )
            return self._active_grant_set(
                session, skill_id=skill_id, actor_id=actor_id, env=env
            )

    @staticmethod
    def _require_binding(
        session, *, space_id: int, skill_id: int, env: str, lock=False
    ):
        query = session.query(SkillSpaceBinding).filter(
            SkillSpaceBinding.space_id == space_id,
            SkillSpaceBinding.skill_id == skill_id,
            SkillSpaceBinding.env == env,
        )
        if lock:
            query = query.with_for_update()
        binding = query.one_or_none()
        if binding is None:
            raise SpaceSkillGrantNotFoundError("space skill not found")
        return binding

    def _lock_binding_and_require_owner(
        self, session, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> None:
        self._require_binding(
            session, space_id=space_id, skill_id=skill_id, env=env, lock=True
        )
        if (
            self._active_member(
                session, space_id=space_id, user_id=actor_id, env=env, lock=True
            )
            is None
        ):
            raise SpaceSkillGrantForbiddenError("active membership required")
        owner = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == actor_id,
                SkillGrant.env == env,
                SkillGrant.role == "OWNER",
                SkillGrant.status == "ACTIVE",
                SkillGrant.owner_slot == 1,
            )
            .with_for_update()
            .one_or_none()
        )
        if owner is None:
            raise SpaceSkillGrantForbiddenError("skill owner required")

    @staticmethod
    def _active_member(session, *, space_id: int, user_id: str, env: str, lock=False):
        query = session.query(SpaceMemberModel).filter(
            SpaceMemberModel.space_id == space_id,
            SpaceMemberModel.user_id == user_id,
            SpaceMemberModel.env == env,
            SpaceMemberModel.status == "ACTIVE",
        )
        if lock:
            query = query.with_for_update()
        return query.one_or_none()

    def _require_active_member(
        self, session, *, space_id: int, user_id: str, env: str
    ) -> SpaceMemberModel:
        member = self._active_member(
            session, space_id=space_id, user_id=user_id, env=env, lock=True
        )
        if member is None:
            raise SpaceSkillGrantMemberRequiredError("active space member required")
        return member

    @staticmethod
    def _active_grant_set(
        session, *, skill_id: int, actor_id: str, env: str
    ) -> SpaceSkillGrantSetRecord:
        grants = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.env == env,
                SkillGrant.status == "ACTIVE",
            )
            .order_by(SkillGrant.role.asc(), SkillGrant.user_id.asc())
            .all()
        )
        owners = [grant for grant in grants if grant.role == "OWNER"]
        if len(owners) != 1:
            raise SpaceSkillGrantConflictError("skill must have one active owner")
        owner = owners[0]
        actor = next((grant for grant in grants if grant.user_id == actor_id), None)
        return {
            "owner": {"user_id": owner.user_id, "role": "OWNER"},
            "managers": [
                {"user_id": grant.user_id, "role": "MANAGER"}
                for grant in grants
                if grant.role == "MANAGER"
            ],
            "actor_role": actor.role if actor is not None else None,
        }

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
