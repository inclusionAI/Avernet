"""Atomic, environment-scoped persistence for maintenance operations."""

from injector import inject

from sqlalchemy import func

from agentclaw.community.core.repository.protocols.space_persistence_effects import (
    SpacePersistenceEffects,
)
from agentclaw.community.core.repository.protocols.spaces import (
    SpaceManagementRepositoryProtocol,
)
from agentclaw.community.core.spaces.management_models import (
    ManagedSpace,
    ManagementError,
    MemberPageQuery,
    SpacePageQuery,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceModel,
    SpaceMemberModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class SpaceManagementRepository(SpaceManagementRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin, effects: SpacePersistenceEffects) -> None:
        self._db = db
        self._effects = effects

    def _space(self, db, space_id: int, env: str, *, lock: bool = False):
        query = db.query(SpaceModel).filter(
            SpaceModel.id == space_id,
            SpaceModel.env == env,
            SpaceModel.deleted_at.is_(None),
        )
        space = (
            self._effects.lock_space(db, space_id=space_id, environment=env)
            if lock else query.one_or_none()
        )
        if space is None:
            raise ManagementError("SPACE_NOT_FOUND")
        if space.space_type != "TEAM":
            raise ManagementError("SPACE_TYPE_NOT_SUPPORTED")
        return space

    @staticmethod
    def _members(db, space_id: int, env: str):
        return db.query(SpaceMemberModel).filter(
            SpaceMemberModel.space_id == space_id,
            SpaceMemberModel.env == env,
        )

    def _detail(self, db, space, env: str) -> ManagedSpace:
        members = self._members(db, space.id, env).filter(
            SpaceMemberModel.status == "ACTIVE"
        )
        return ManagedSpace(
            space=space.to_record(),
            member_count=members.count(),
            admin_count=members.filter(
                SpaceMemberModel.role.in_(["ADMIN", "ADMINISTRATOR"])
            ).count(),
        )

    def get_team(self, *, space_id: int, env: str) -> ManagedSpace:
        with self._db.orm_session() as db:
            return self._detail(db, self._space(db, space_id, env), env)

    def list_teams(self, *, query: SpacePageQuery, env: str) -> dict:
        with self._db.orm_session() as db:
            rows = db.query(SpaceModel).filter(
                SpaceModel.env == env,
                SpaceModel.space_type == "TEAM",
                SpaceModel.deleted_at.is_(None),
            )
            if query.keyword:
                rows = rows.filter(SpaceModel.name.ilike(f"%{query.keyword}%"))
            if query.creator_user_id:
                rows = rows.filter(SpaceModel.created_by == query.creator_user_id)
            total = rows.count()
            page = (
                rows.order_by(SpaceModel.gmt_modified.desc(), SpaceModel.id.desc())
                .offset((query.page_no - 1) * query.page_size)
                .limit(query.page_size)
                .all()
            )
            return dict(
                total=total,
                items=[self._detail(db, row, env) for row in page],
                page_no=query.page_no,
                page_size=query.page_size,
            )

    def rename_team(self, *, space_id: int, name: str, actor_id: str, env: str) -> dict:
        with self._db.transactional_orm_session() as db:
            space = self._space(db, space_id, env, lock=True)
            # Exactly the existing name scope/comparison; no global normalization.
            conflict = (
                db.query(SpaceModel.id)
                .filter(
                    SpaceModel.env == env,
                    SpaceModel.created_by == space.created_by,
                    SpaceModel.space_type == "TEAM",
                    SpaceModel.deleted_at.is_(None),
                    SpaceModel.name == name,
                    SpaceModel.id != space_id,
                )
                .first()
            )
            if conflict:
                raise ManagementError("SPACE_NAME_ALREADY_EXISTS")
            changed = space.name != name
            if changed:
                space.name = name
                space.updated_by = actor_id
                space.gmt_modified = func.now()
                db.flush()
                db.refresh(space)
            if changed:
                self._effects.record_membership(db, space)
            return dict(space=self._detail(db, space, env), changed=changed)

    def delete_team(self, *, space_id: int, with_members: bool, env: str) -> dict:
        with self._db.transactional_orm_session() as db:
            space = self._space(db, space_id, env, lock=True)
            members = self._members(db, space_id, env)
            count = members.count()
            if count and not with_members:
                raise ManagementError(
                    "SPACE_HAS_MEMBERS", space_id=space_id, member_count=count
                )
            self._effects.record_membership(db, space, disabled=True)
            removed = members.delete(synchronize_session=False) if with_members else 0
            db.delete(space)
            db.flush()
            return dict(space_id=space_id, deleted=True, removed_member_count=removed)

    def list_members(self, *, space_id: int, query: MemberPageQuery, env: str) -> dict:
        with self._db.orm_session() as db:
            space = self._space(db, space_id, env)
            rows = self._members(db, space_id, env)
            if query.status != "ALL":
                rows = rows.filter(SpaceMemberModel.status == query.status)
            if query.keyword:
                rows = rows.filter(SpaceMemberModel.user_id.ilike(f"%{query.keyword}%"))
            if query.role:
                roles = (
                    ["ADMIN", "ADMINISTRATOR"] if query.role == "ADMIN" else ["MEMBER"]
                )
                rows = rows.filter(SpaceMemberModel.role.in_(roles))
            total = rows.count()
            page = (
                rows.order_by(
                    SpaceMemberModel.gmt_modified.desc(), SpaceMemberModel.id.desc()
                )
                .offset((query.page_no - 1) * query.page_size)
                .limit(query.page_size)
                .all()
            )
            return dict(
                total=total,
                items=[
                    dict(
                        **row.to_record().model_dump(),
                        status=row.status,
                        is_creator=row.user_id == space.created_by,
                    )
                    for row in page
                ],
                page_no=query.page_no,
                page_size=query.page_size,
            )

    def add_members(
        self, *, space_id: int, members: dict[str, str | None], actor_id: str, env: str
    ) -> dict:
        with self._db.transactional_orm_session() as db:
            space = self._space(db, space_id, env, lock=True)
            existing = {
                row.user_id: row
                for row in self._members(db, space_id, env)
                .filter(
                    SpaceMemberModel.user_id.in_(members),
                )
                .all()
            }
            result = dict(
                space_id=space_id,
                added_user_ids=[],
                existing_user_ids=[],
                reactivated_user_ids=[],
            )
            for user_id, user_name in members.items():
                row = existing.get(user_id)
                if row is None:
                    db.add(
                        SpaceMemberModel(
                            space_id=space_id,
                            user_id=user_id,
                            user_name=user_name,
                            role="MEMBER",
                            status="ACTIVE",
                            env=env,
                            created_by=actor_id,
                        )
                    )
                    result["added_user_ids"].append(user_id)
                elif row.status == "ACTIVE":
                    result["existing_user_ids"].append(user_id)
                else:
                    row.status, row.role, row.user_name = "ACTIVE", "MEMBER", user_name
                    row.removed_at = row.removed_by = None
                    row.gmt_modified = func.now()
                    result["reactivated_user_ids"].append(user_id)
            db.flush()
            self._effects.record_membership(db, space)
            return result

    def remove_members(self, *, space_id: int, user_ids: list[str], env: str) -> dict:
        with self._db.transactional_orm_session() as db:
            space = self._space(db, space_id, env, lock=True)
            rows = self._members(db, space_id, env).filter(
                SpaceMemberModel.user_id.in_(user_ids)
            )
            found = {row.user_id for row in rows.all()}
            rows.delete(synchronize_session=False)
            if found:
                self._effects.record_membership(db, space)
            return dict(
                space_id=space_id,
                removed_user_ids=[uid for uid in user_ids if uid in found],
                missing_user_ids=[uid for uid in user_ids if uid not in found],
            )

    def set_member_roles(
        self, *, space_id: int, user_ids: list[str], role: str, env: str
    ) -> dict:
        with self._db.transactional_orm_session() as db:
            space = self._space(db, space_id, env, lock=True)
            rows = {
                row.user_id: row
                for row in self._members(db, space_id, env)
                .filter(
                    SpaceMemberModel.user_id.in_(user_ids),
                    SpaceMemberModel.status == "ACTIVE",
                )
                .all()
            }
            missing = [uid for uid in user_ids if uid not in rows]
            if missing:
                raise ManagementError(
                    "SPACE_MEMBERS_NOT_FOUND", missing_user_ids=missing
                )
            updated, unchanged = [], []
            for uid in user_ids:
                row = rows[uid]
                if row.to_record().role.value == role:
                    unchanged.append(uid)
                else:
                    row.role, row.gmt_modified = role, func.now()
                    updated.append(uid)
            db.flush()
            if updated:
                self._effects.record_membership(db, space)
            return dict(
                space_id=space_id,
                role=role,
                updated_user_ids=updated,
                unchanged_user_ids=unchanged,
            )
