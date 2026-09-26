"""Unified SkillMember repository (prod OceanBase + local SQLite).

One ORM implementation behind the ``SkillMemberRepository`` Protocol.
The only per-environment difference is the injected
:class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes, so this single body runs unchanged on
OceanBase (prod) and SQLite (local), collapsing the previous
raw-SQL/ORM twins so CI exercises the prod path too.

Prod-twin parity:

- ``add_member`` does the existence check + insert in a single
  session (prod twin: SELECT then INSERT inside the same
  ``conn.session()`` context; local twin: same shape via ORM). The
  ValueError on duplicate matches both twins.
- ``update_member_role`` / ``remove_member`` raise ValueError when
  the row is missing (prod parity — both twins did this).
- ``gmt_create`` / ``gmt_modified`` are model-defaulted
  (``default=func.now()`` on ``AcSkillMember``; prod DDL also
  defaults both to ``CURRENT_TIMESTAMP``); bulk-shaped writes are
  avoided here because every mutation touches one row.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.skill_center import SkillMemberRepository as SkillMemberRepositoryProtocol


logger = get_logger()


class SkillMemberRepository(
    SkillMemberRepositoryProtocol,
):
    """Unified ``SkillMemberRepository`` Protocol implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.core.models.skill import AcSkillMember

        self._db = db
        self.Model = AcSkillMember

    def get_members_by_skill_uuid(
        self, skill_uuid: str
    ) -> List[Dict[str, Any]]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(self.Model.skill_uuid == skill_uuid)
                .order_by(self.Model.gmt_create.asc())
                .all()
            )
            return [r.to_dict() for r in rows]

    def get_member(
        self, skill_uuid: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.skill_uuid == skill_uuid,
                    self.Model.user_id == user_id,
                )
                .first()
            )
            return row.to_dict() if row else None

    def add_member(
        self, skill_uuid: str, user_id: str, role: str = "member"
    ) -> Dict[str, Any]:
        if role not in ("admin", "member"):
            raise ValueError(
                f"Invalid role: {role}. Must be 'admin' or 'member'"
            )
        with self._db.orm_session() as db:
            existing = (
                db.query(self.Model.id)
                .filter(
                    self.Model.skill_uuid == skill_uuid,
                    self.Model.user_id == user_id,
                )
                .first()
            )
            if existing:
                raise ValueError(
                    f"User {user_id} is already a member of skill {skill_uuid}"
                )
            member = self.Model(
                skill_uuid=skill_uuid,
                user_id=user_id,
                role=role,
            )
            db.add(member)
            db.flush()
            result = member.to_dict()
            logger.info(
                "[SkillMemberRepo] Added member skill=%s user=%s role=%s",
                skill_uuid, user_id, role,
            )
            return result

    def remove_member(self, skill_uuid: str, user_id: str) -> bool:
        with self._db.orm_session() as db:
            member = (
                db.query(self.Model)
                .filter(
                    self.Model.skill_uuid == skill_uuid,
                    self.Model.user_id == user_id,
                )
                .first()
            )
            if not member:
                raise ValueError(
                    f"Member not found: user_id={user_id}, "
                    f"skill_uuid={skill_uuid}"
                )
            db.delete(member)
            logger.info(
                "[SkillMemberRepo] Removed member skill=%s user=%s",
                skill_uuid, user_id,
            )
            return True

    def update_member_role(
        self, skill_uuid: str, user_id: str, role: str
    ) -> Dict[str, Any]:
        if role not in ("admin", "member"):
            raise ValueError(
                f"Invalid role: {role}. Must be 'admin' or 'member'"
            )
        with self._db.orm_session() as db:
            member = (
                db.query(self.Model)
                .filter(
                    self.Model.skill_uuid == skill_uuid,
                    self.Model.user_id == user_id,
                )
                .first()
            )
            if not member:
                raise ValueError(
                    f"Member not found: user_id={user_id}, "
                    f"skill_uuid={skill_uuid}"
                )
            member.role = role
            member.gmt_modified = func.now()
            db.flush()
            result = member.to_dict()
            logger.info(
                "[SkillMemberRepo] Updated role skill=%s user=%s role=%s",
                skill_uuid, user_id, role,
            )
            return result

    def is_member(self, skill_uuid: str, user_id: str) -> bool:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model.id)
                .filter(
                    self.Model.skill_uuid == skill_uuid,
                    self.Model.user_id == user_id,
                )
                .first()
            )
            return row is not None

    def get_member_role(
        self, skill_uuid: str, user_id: str
    ) -> Optional[str]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model.role)
                .filter(
                    self.Model.skill_uuid == skill_uuid,
                    self.Model.user_id == user_id,
                )
                .first()
            )
            return row[0] if row else None

    def get_skill_uuids_by_user_id(self, user_id: str) -> List[str]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model.skill_uuid)
                .filter(self.Model.user_id == user_id)
                .all()
            )
            return [r[0] for r in rows]

    def has_admin_role(self, skill_uuid: str, user_id: str) -> bool:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model.role)
                .filter(
                    self.Model.skill_uuid == skill_uuid,
                    self.Model.user_id == user_id,
                )
                .first()
            )
            return row is not None and row[0] == "admin"

    def get_members_by_skill_uuids(
        self, skill_uuids: List[str]
    ) -> Dict[str, List[Dict[str, str]]]:
        if not skill_uuids:
            return {}
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(self.Model.skill_uuid.in_(skill_uuids))
                .all()
            )
            members_map: Dict[str, List[Dict[str, str]]] = {}
            for m in rows:
                members_map.setdefault(m.skill_uuid, []).append(
                    {"user_id": str(m.user_id), "role": m.role}
                )
            return members_map
