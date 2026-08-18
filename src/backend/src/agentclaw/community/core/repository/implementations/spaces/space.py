"""Unified ORM repository for spaces and members."""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

from injector import inject
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.spaces.errors import (
    SpaceAlreadyExistsError,
    SpaceMemberAlreadyExistsError,
)
from agentclaw.community.core.spaces.models import (
    PersonalSpaceLookupRecord,
    SpaceJoinStatus,
    SpaceMemberSummaryRecord,
    SpaceRole,
    SpaceSummaryRecord,
    SpaceType,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class SpaceRepository(SpaceRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._Space = SpaceModel
        self._Member = SpaceMemberModel

    @staticmethod
    def _new_code() -> str:
        return f"spc-{uuid4().hex[:20]}"

    def initialize_personal(self, *, user_id: str, env: str):
        try:
            with self._db.transactional_orm_session() as db:
                existing = (
                    db.query(self._Space)
                    .filter(
                        self._Space.personal_owner_id == user_id,
                        self._Space.env == env,
                    )
                    .one_or_none()
                )
                if existing is not None:
                    return existing.to_record(), False

                space = self._Space(
                    space_code=self._new_code(),
                    space_type=SpaceType.PERSONAL.value,
                    name="个人空间",
                    personal_owner_id=user_id,
                    env=env,
                    created_by=user_id,
                    updated_by=user_id,
                )
                db.add(space)
                db.flush()
                db.add(
                    self._Member(
                        space_id=space.id,
                        user_id=user_id,
                        role=SpaceRole.OWNER.value,
                        env=env,
                        created_by=user_id,
                    )
                )
                db.flush()
                db.refresh(space)
                return space.to_record(), True
        except IntegrityError:
            # Concurrent initialization: the unique personal-owner key decides
            # the winner; the loser returns the same established space.
            with self._db.orm_session() as db:
                existing = (
                    db.query(self._Space)
                    .filter(
                        self._Space.personal_owner_id == user_id,
                        self._Space.env == env,
                    )
                    .one_or_none()
                )
                if existing is None:
                    raise
                return existing.to_record(), False

    @contextmanager
    def create_team_transaction(self, *, name: str, creator_id: str, env: str):
        try:
            with self._db.transactional_orm_session() as db:
                space = self._Space(
                    space_code=self._new_code(),
                    space_type=SpaceType.TEAM.value,
                    name=name,
                    personal_owner_id=None,
                    env=env,
                    created_by=creator_id,
                    updated_by=creator_id,
                )
                db.add(space)
                db.flush()
                db.add(
                    self._Member(
                        space_id=space.id,
                        user_id=creator_id,
                        role=SpaceRole.OWNER.value,
                        env=env,
                        created_by=creator_id,
                    )
                )
                db.flush()
                db.refresh(space)
                # The caller performs required external synchronization while
                # this transaction remains open. Any exception raised before
                # this context exits rolls both rows back. After SC succeeds,
                # the caller assigns its returned team id to this record; the
                # repository persists it in the same OB transaction.
                record = space.to_record()
                yield record
                space.sc_team_id = record.sc_team_id
                db.flush()
        except IntegrityError as exc:
            raise SpaceAlreadyExistsError("unable to allocate space code") from exc

    def get_space(self, *, space_id: int, env: str):
        with self._db.orm_session() as db:
            row = (
                db.query(self._Space)
                .filter(self._Space.id == space_id, self._Space.env == env)
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def batch_query_personal(
        self, *, user_ids: list[str], env: str
    ) -> list[PersonalSpaceLookupRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Space.personal_owner_id, self._Space.id)
                .filter(
                    self._Space.personal_owner_id.in_(user_ids),
                    self._Space.space_type == SpaceType.PERSONAL.value,
                    self._Space.env == env,
                )
                .all()
            )
        space_ids = {str(user_id): int(space_id) for user_id, space_id in rows}
        return [
            PersonalSpaceLookupRecord(
                user_id=user_id,
                space_id=space_ids.get(user_id),
                found=user_id in space_ids,
            )
            for user_id in user_ids
        ]

    def list_spaces(
        self,
        *,
        user_id: str,
        env: str,
        keyword: str | None,
        space_type: str | None,
        offset: int,
        limit: int,
    ):
        with self._db.orm_session() as db:
            query = db.query(self._Space).filter(
                self._Space.env == env,
                or_(
                    self._Space.space_type != SpaceType.PERSONAL.value,
                    self._Space.personal_owner_id == user_id,
                ),
            )
            if keyword:
                query = query.filter(self._Space.name.ilike(f"%{keyword}%"))
            if space_type:
                query = query.filter(self._Space.space_type == space_type)
            total = query.count()
            rows = (
                query.order_by(self._Space.gmt_modified.desc(), self._Space.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            items = []
            for row in rows:
                current = (
                    db.query(self._Member)
                    .filter(
                        self._Member.space_id == row.id,
                        self._Member.user_id == user_id,
                        self._Member.env == env,
                    )
                    .one_or_none()
                )
                member_count = (
                    db.query(func.count(self._Member.id))
                    .filter(self._Member.space_id == row.id, self._Member.env == env)
                    .scalar()
                    or 0
                )
                owner_count = (
                    db.query(func.count(self._Member.id))
                    .filter(
                        self._Member.space_id == row.id,
                        self._Member.role == SpaceRole.OWNER.value,
                        self._Member.env == env,
                    )
                    .scalar()
                    or 0
                )
                role = SpaceRole(current.role) if current is not None else None
                items.append(
                    SpaceSummaryRecord(
                        space=row.to_record(),
                        current_user_role=role,
                        join_status=(
                            SpaceJoinStatus.JOINED
                            if current is not None
                            else SpaceJoinStatus.NOT_JOINED
                        ),
                        member_count=member_count,
                        owner_count=owner_count,
                    )
                )
            return total, items

    def get_member(self, *, space_id: int, user_id: str, env: str):
        with self._db.orm_session() as db:
            row = (
                db.query(self._Member)
                .filter(
                    self._Member.space_id == space_id,
                    self._Member.user_id == user_id,
                    self._Member.env == env,
                )
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def list_members(
        self,
        *,
        space_id: int,
        env: str,
        keyword: str | None,
        offset: int,
        limit: int,
    ):
        with self._db.orm_session() as db:
            query = db.query(self._Member).filter(
                self._Member.space_id == space_id, self._Member.env == env
            )
            if keyword:
                query = query.filter(self._Member.user_id.ilike(f"%{keyword}%"))
            total = query.count()
            rows = (
                query.order_by(self._Member.gmt_modified.desc(), self._Member.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            space = (
                db.query(self._Space)
                .filter(self._Space.id == space_id, self._Space.env == env)
                .one_or_none()
            )
            creator_id = space.created_by if space is not None else ""
            return total, [
                SpaceMemberSummaryRecord(
                    member=row.to_record(), is_creator=row.user_id == creator_id
                )
                for row in rows
            ]

    def add_member(
        self,
        *,
        space_id: int,
        user_id: str,
        role: SpaceRole,
        creator_id: str,
        env: str,
    ):
        try:
            with self._db.orm_session() as db:
                row = self._Member(
                    space_id=space_id,
                    user_id=user_id,
                    role=role.value,
                    env=env,
                    created_by=creator_id,
                )
                db.add(row)
                db.flush()
                db.refresh(row)
                return row.to_record()
        except IntegrityError as exc:
            raise SpaceMemberAlreadyExistsError("space member already exists") from exc

    def delete_member(self, *, space_id: int, user_id: str, env: str) -> bool:
        with self._db.orm_session() as db:
            deleted = (
                db.query(self._Member)
                .filter(
                    self._Member.space_id == space_id,
                    self._Member.user_id == user_id,
                    self._Member.env == env,
                )
                .delete(synchronize_session=False)
            )
            return deleted > 0

    def update_member_role(
        self, *, space_id: int, user_id: str, role: SpaceRole, env: str
    ):
        with self._db.orm_session() as db:
            row = (
                db.query(self._Member)
                .filter(
                    self._Member.space_id == space_id,
                    self._Member.user_id == user_id,
                    self._Member.env == env,
                )
                .one_or_none()
            )
            if row is None:
                return None
            row.role = role.value
            row.gmt_modified = func.now()
            db.flush()
            db.refresh(row)
            return row.to_record()
