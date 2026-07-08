"""Unified Resource repository (prod ZDAS + local SQLite).

One ORM implementation behind the ``ResourceRepositoryProtocol``.
The only per-environment difference is the injected
:class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes, so this single body runs unchanged on
OceanBase (prod) and SQLite (local), collapsing the previous
raw-SQL/ORM twins so CI exercises the prod path too.

Notable column quirks (preserved verbatim):

- ``ac_resource.gmt_created`` (not ``gmt_create``) — typo carried in
  the prod DDL; the ORM model declares it as-is.
- ``ResourceModel.user_id`` and ``created_by`` are varchar/string
  fields. Callers pass ``str`` and the repository preserves string
  semantics for writes and filters.
- ``to_dict()`` returns ``id`` / ``user_id`` / ``created_by`` as
  strings and maps the ``meta`` column to a ``metadata`` key —
  established public contract from both twins.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.resources.repository.protocol import (
    ResourceRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class ResourceRepository(ResourceRepositoryProtocol):
    """Unified ORM-backed ``ResourceRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.plugin_api.models import ResourceModel

        self._db = db
        self.Model = ResourceModel

    def _optional_str(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)

    def get_by_id(self, resource_id: str) -> Optional[dict]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.id == int(resource_id))
                .first()
            )
            return row.to_dict() if row else None

    def get_by_path(
        self, path: str, bolt_id: Optional[str] = None
    ) -> Optional[dict]:
        effective_bolt_id = bolt_id if bolt_id else "default"
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(
                    self.Model.resource_type == "file",
                    self.Model.status != "deleted",
                    self.Model.bolt_id == effective_bolt_id,
                )
                .all()
            )
            for row in rows:
                attrs = json.loads(row.attributes or "{}")
                if attrs.get("path") == path:
                    return row.to_dict()
            return None

    def list_resources(
        self,
        resource_type: Optional[str] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        bolt_id: Optional[str] = None,
    ) -> List[dict]:
        effective_bolt_id = bolt_id if bolt_id else "default"
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.bolt_id == effective_bolt_id
            )
            if resource_type:
                query = query.filter(self.Model.resource_type == resource_type)
            if user_id is not None:
                query = query.filter(self.Model.user_id == str(user_id))
            if status:
                query = query.filter(self.Model.status == status)
            else:
                query = query.filter(self.Model.status != "deleted")

            rows = query.order_by(self.Model.gmt_created.desc()).all()
            results = [r.to_dict() for r in rows]
            if parent_path is not None:
                results = [
                    r
                    for r in results
                    if (r.get("attributes") or {}).get("parent_path")
                    == parent_path
                ]
            return results

    def create(self, resource_data: dict) -> dict:
        with self._db.orm_session() as db:
            row = self.Model(
                name=resource_data["name"],
                resource_type=resource_data["resource_type"],
                status=resource_data.get("status", "active"),
                attributes=json.dumps(resource_data.get("attributes", {})),
                meta=(
                    json.dumps(resource_data.get("metadata"))
                    if resource_data.get("metadata")
                    else None
                ),
                user_id=self._optional_str(resource_data.get("user_id")),
                created_by=self._optional_str(resource_data.get("created_by")),
                source=resource_data.get("source"),
                bolt_id=resource_data.get("bolt_id") or "default",
                env=get_current_env(),
            )
            db.add(row)
            db.flush()
            return row.to_dict()

    def update(
        self, resource_id: str, resource_data: dict
    ) -> Optional[dict]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.id == int(resource_id))
                .first()
            )
            if not row:
                return None
            for key, value in resource_data.items():
                # Map 'metadata' (public key) → 'meta' (SQL column —
                # SQLAlchemy reserves 'metadata'); preserved from both
                # twins.
                if key == "metadata":
                    key = "meta"
                if key in ("attributes", "meta") and isinstance(value, dict):
                    value = json.dumps(value)
                if key in ("user_id", "created_by"):
                    value = self._optional_str(value)
                setattr(row, key, value)
            db.flush()
            return row.to_dict()

    def delete(self, resource_id: str) -> bool:
        # Single blind UPDATE — prod parity (prod twin: one UPDATE,
        # rowcount > 0). mysqlconnector sets CLIENT_FOUND_ROWS so
        # rowcount = rows matched, same as SQLite.
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(self.Model.id == int(resource_id))
                .update(
                    {
                        self.Model.status: "deleted",
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
            return rowcount > 0

    def hard_delete(self, resource_id: str) -> bool:
        # Single blind DELETE — prod parity (prod twin: one DELETE,
        # rowcount > 0).
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(self.Model.id == int(resource_id))
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def count_resources(
        self,
        resource_type: Optional[str] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        bolt_id: Optional[str] = None,
    ) -> int:
        return len(
            self.list_resources(
                resource_type=resource_type,
                parent_path=parent_path,
                user_id=user_id,
                status=status,
                bolt_id=bolt_id,
            )
        )
