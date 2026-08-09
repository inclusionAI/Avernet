"""Unified SkillCategory repository (prod the relational store + local SQLite).

One ORM implementation behind ``SkillCategoryRepository``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local),
collapsing the previous raw-SQL/ORM twins so CI exercises the prod path
too.

Behavior matches the prior local twin (the CI-covered contract);
``SkillCategory.to_dict()`` already matches the prod twin's row shape
(id-as-str, ISO timestamps). Production DDL parity for
``ac_skill_category`` is a Pre acceptance record (round-2 spec).
"""
from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.skill_center import SkillCategoryRepository


class SkillCategoryRepository(
    SkillCategoryRepository,
):
    """Unified ``SkillCategoryRepository`` Protocol implementation."""

    @inject
    def __init__(self, db: DatabasePlugin):
        from agentclaw.community.core.models.skill import SkillCategory

        self._db = db
        self.SkillCategory = SkillCategory

    def list_active(self) -> List[Dict[str, Any]]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.SkillCategory)
                .filter(self.SkillCategory.status == 1)
                .order_by(
                    self.SkillCategory.level, self.SkillCategory.sort_order
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def get_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.SkillCategory)
                .filter(self.SkillCategory.code == code)
                .first()
            )
            return row.to_dict() if row else None

    def get_by_path(self, path: str) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.SkillCategory)
                .filter(self.SkillCategory.path == path)
                .first()
            )
            return row.to_dict() if row else None

    def create(
        self,
        code: str,
        name: str,
        parent_code: str,
        path: str,
        level: int,
        sort_order: int,
    ) -> Dict[str, Any]:
        with self._db.orm_session() as db:
            category = self.SkillCategory(
                code=code,
                name=name,
                parent_code=parent_code or "",
                path=path,
                level=level,
                sort_order=sort_order,
                status=1,
            )
            db.add(category)
            db.flush()
            # gmt_created/gmt_modified are DB server defaults (func.now());
            # refresh to return the DB-generated values.
            db.refresh(category)
            return category.to_dict()

    def _update_values(self, fields: dict) -> dict:
        values = {
            getattr(self.SkillCategory, k): v
            for k, v in fields.items()
            if hasattr(self.SkillCategory, k) and v is not None
        }
        values[self.SkillCategory.gmt_modified] = func.now()
        return values

    def update_by_path(
        self, path: str, **fields
    ) -> Optional[Dict[str, Any]]:
        # Single atomic UPDATE (one query, atomic on both backends incl.
        # prod AUTOCOMMIT) — no SELECT-then-UPDATE. Then one SELECT to
        # return the row, matching the prior prod twin's
        # `UPDATE ... WHERE path=%s; return self.get_by_path(path)`.
        with self._db.orm_session() as db:
            db.query(self.SkillCategory).filter(
                self.SkillCategory.path == path
            ).update(self._update_values(fields), synchronize_session=False)
        return self.get_by_path(path)

    def update(self, code: str, **fields) -> Optional[Dict[str, Any]]:
        with self._db.orm_session() as db:
            db.query(self.SkillCategory).filter(
                self.SkillCategory.code == code
            ).update(self._update_values(fields), synchronize_session=False)
        return self.get_by_code(code)

    def list_descendant_codes(self, path: str) -> List[str]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.SkillCategory.code)
                .filter(
                    self.SkillCategory.path.like(f"{path}%"),
                    self.SkillCategory.status == 1,
                )
                .all()
            )
            return [r[0] for r in rows]
