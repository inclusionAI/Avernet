"""Unified BotPublish repository (prod ZDAS + local SQLite).

One ORM implementation behind ``BotPublishRepositoryProtocol``. The
only per-environment difference is the injected
:class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes, so this single body runs unchanged on
OceanBase (prod) and SQLite (local), collapsing the previous
raw-SQL/ORM twins so CI exercises the prod path too.

Prod-twin parity (the raw-SQL ``ZdasBotPublishRepository`` is the
reference):

- ``insert`` is a **plain INSERT** (``db.add`` + ``db.flush``) then
  returns ``get_by_id(row.id)`` — never an upsert, despite the
  ``uk_oi_p_b_v`` unique key (publish records are versioned/
  append-only; prod has no ``ON DUPLICATE KEY``).
- ``update_status`` / ``update_status_with_ext`` are **single
  optimistic-lock UPDATEs**: a bulk ``.update(...,
  synchronize_session=False)`` with ``WHERE id [AND status =
  source_status]``; when ``source_status`` is set and 0 rows match
  the source state did not match → return ``None`` (no SELECT-first
  guard). mysqlconnector sets CLIENT_FOUND_ROWS so rowcount = rows
  matched, identical on SQLite. ``ext`` is stored as a JSON string
  (``json.dumps(..., ensure_ascii=False)``) — prod parity (the old
  SQLite twin stored a raw dict in ``update_status_with_ext``; that
  divergence is dropped).
- ``update_version`` / ``update_last_pub_id`` are single UPDATEs;
  ``delete`` is a single **hard** ``DELETE`` (prod parity — not a
  soft delete).
- ``gmt_modified`` is set ``func.now()`` DB-side on every UPDATE to
  match prod's literal ``gmt_modified = NOW()`` / the model's
  ``onupdate`` (a Core/bulk UPDATE fires neither on SQLite).
- Reads return ``BotPublishRecord`` via ``BotPublishModel.to_record()``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


class BotPublishRepository:
    """Unified ORM ``BotPublishRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self.Model = BotPublishModel

    # ── insert (plain INSERT — never an upsert) ─────────────────

    def insert(self, data: Dict[str, Any]) -> BotPublishRecord:
        ext = data.get("ext")
        ext_json = (
            json.dumps(ext, ensure_ascii=False) if ext is not None else None
        )
        with self._db.orm_session() as db:
            row = self.Model(
                source_bot_pk=data["source_bot_pk"],
                source_bot_id=data["source_bot_id"],
                publish_bot_id=data["publish_bot_id"],
                name=data["name"],
                description=data.get("description"),
                owner_id=data["owner_id"],
                owner_name=data.get("owner_name"),
                status=data.get("status", PublishStatus.DRAFT),
                version=data.get("version"),
                last_pub_id=data.get("last_pub_id", 0),
                env=data.get("env", get_current_env()),
                ext=ext_json,
                permission_owner=data["permission_owner"],
            )
            db.add(row)
            db.flush()
            new_id = row.id
            logger.info("[insert] inserted bot publish id=%s", new_id)
        # Re-read after commit — prod returns get_by_id(inserted_id),
        # so the returned record carries DB-populated gmt_create /
        # gmt_modified (not None from a pre-commit ORM object).
        return self.get_by_id(new_id)

    # ── queries ─────────────────────────────────────────────────

    def get_by_id(self, publish_id: int) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.id == publish_id)
                .first()
            )
            return row.to_record() if row else None

    def get_by_publish_bot_id(
        self,
        publish_bot_id: str,
        owner_id: str,
        env: str,
        publish_status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.publish_bot_id == publish_bot_id,
                self.Model.owner_id == owner_id,
                self.Model.env == env,
            )
            if publish_status:
                query = query.filter(self.Model.status == publish_status)
            row = query.order_by(self.Model.version.desc()).first()
            return row.to_record() if row else None

    def get_draft_by_publish_bot_id(
        self,
        publish_bot_id: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.publish_bot_id == publish_bot_id,
                    self.Model.env == env,
                    self.Model.status == PublishStatus.DRAFT,
                )
                .order_by(self.Model.version.desc())
                .first()
            )
            return row.to_record() if row else None

    def get_by_publish_bot_id_and_version(
        self,
        publish_bot_id: str,
        owner_id: str,
        version: int,
        env: str,
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.publish_bot_id == publish_bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.version == version,
                    self.Model.env == env,
                )
                .first()
            )
            return row.to_record() if row else None

    def list_by_owner(
        self,
        owner_id: str,
        env: str,
        status: Optional[str] = None,
    ) -> List[BotPublishRecord]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.owner_id == owner_id,
                self.Model.env == env,
            )
            if status:
                query = query.filter(self.Model.status == status)
            rows = query.order_by(self.Model.gmt_create.desc()).all()
            return [r.to_record() for r in rows]

    def list_by_source_bot(
        self,
        source_bot_pk: int,
        env: str,
    ) -> List[BotPublishRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(
                    self.Model.source_bot_pk == source_bot_pk,
                    self.Model.env == env,
                )
                .order_by(self.Model.gmt_create.desc())
                .all()
            )
            return [r.to_record() for r in rows]

    def list_by_status(
        self,
        status: str,
        env: str,
    ) -> List[BotPublishRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(
                    self.Model.status == status,
                    self.Model.env == env,
                )
                .order_by(self.Model.gmt_create.desc())
                .all()
            )
            return [r.to_record() for r in rows]

    def get_latest_by_source_bot_id_and_owner_and_status(
        self,
        source_bot_id: str,
        owner_id: str,
        status: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.source_bot_id == source_bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.status == status,
                    self.Model.env == env,
                )
                .order_by(self.Model.id.desc())
                .first()
            )
            return row.to_record() if row else None

    def get_latest_success_by_source_bot_id(
        self,
        source_bot_id: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.source_bot_id == source_bot_id,
                    self.Model.status == PublishStatus.SUCCESS.value,
                    self.Model.env == env,
                )
                .order_by(self.Model.id.desc())
                .first()
            )
            return row.to_record() if row else None

    def get_by_last_pub_id(
        self,
        last_pub_id: int,
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.last_pub_id == last_pub_id)
                .order_by(self.Model.id.desc())
                .first()
            )
            return row.to_record() if row else None

    # ── updates (single optimistic-lock statements) ─────────────

    def update_status(
        self,
        publish_id: int,
        target_status: str,
        source_status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.id == publish_id
            )
            if source_status is not None:
                query = query.filter(self.Model.status == source_status)
            affected = query.update(
                {
                    self.Model.status: target_status,
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
        if source_status is not None and affected == 0:
            return None
        return self.get_by_id(publish_id)

    def update_status_with_ext(
        self,
        publish_id: int,
        target_status: str,
        ext: Dict[str, Any],
        source_status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        ext_json = json.dumps(ext, ensure_ascii=False)
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.id == publish_id
            )
            if source_status is not None:
                query = query.filter(self.Model.status == source_status)
            affected = query.update(
                {
                    self.Model.status: target_status,
                    self.Model.ext: ext_json,
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
        if source_status is not None and affected == 0:
            return None
        return self.get_by_id(publish_id)

    def update_version(
        self,
        publish_id: int,
        version: int,
        status: Optional[str] = None,
    ) -> Optional[BotPublishRecord]:
        values: dict = {
            self.Model.version: version,
            self.Model.gmt_modified: func.now(),
        }
        if status:
            values[self.Model.status] = status
        with self._db.orm_session() as db:
            db.query(self.Model).filter(
                self.Model.id == publish_id
            ).update(values, synchronize_session=False)
        return self.get_by_id(publish_id)

    def update_last_pub_id(
        self,
        publish_id: int,
        last_pub_id: int,
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            db.query(self.Model).filter(
                self.Model.id == publish_id
            ).update(
                {
                    self.Model.last_pub_id: last_pub_id,
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
        return self.get_by_id(publish_id)

    # ── delete (single hard DELETE — prod parity) ───────────────

    def delete(self, publish_id: int) -> bool:
        with self._db.orm_session() as db:
            affected = (
                db.query(self.Model)
                .filter(self.Model.id == publish_id)
                .delete(synchronize_session=False)
            )
            return affected > 0
