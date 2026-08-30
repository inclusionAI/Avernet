"""Unified BotPublish repository (prod OceanBase + local SQLite).

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

from typing import Any, Dict, List, Optional, Sequence

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.service_bot.repository.config_artifact_offload import (
    ConfigArtifactOffloader,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishLineagePage,
    BotPublishModel,
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.skill_center.offline_policy import require_skill_online
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol

logger = get_logger()


class BotPublishRepository(
    BotPublishRepositoryProtocol,
):
    """Unified ORM ``BotPublishRepositoryProtocol`` implementation.

    An oversized ``ext['config_artifact']`` is offloaded to object storage and
    re-inlined on read by :class:`ConfigArtifactOffloader`; this class owns only
    persistence and delegates the ext ⇄ object-storage transform to it.
    """

    @inject
    def __init__(
        self, db: DatabasePlugin, offload: ConfigArtifactOffloader
    ) -> None:
        self._db = db
        self.Model = BotPublishModel
        # Offloads an oversized config_artifact out of the ext TEXT column.
        self._offload = offload

    def _resolve_record(
        self, record: Optional[BotPublishRecord]
    ) -> Optional[BotPublishRecord]:
        """Re-inline an offloaded artifact on a detached record.

        Called AFTER the DB session closes, so the object-storage network fetch
        never holds a database connection open.
        """
        if record is not None:
            record.ext = self._offload.resolve(record.ext)
        return record

    # ── insert (plain INSERT — never an upsert) ─────────────────

    def insert(self, data: Dict[str, Any]) -> BotPublishRecord:
        ext = data.get("ext")
        env = data.get("env", get_current_env())
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
                env=env,
                # ext set after flush: offloading keys the OSS object by the
                # DB-assigned publish_id, which only exists post-flush. Still a
                # single INSERT in one transaction (a second flush updates ext
                # before commit).
                ext=None,
                permission_owner=data["permission_owner"],
            )
            db.add(row)
            db.flush()
            new_id = row.id
            # ext + upload only after flush: the OSS key is content-addressed
            # under the DB-assigned publish_id. The upload runs inside the txn so
            # a put failure rolls the whole INSERT back (no dangling marker row).
            ext_json, pending = self._offload.prepare(ext, new_id, env)
            row.ext = ext_json
            self._offload.upload(pending)
            db.flush()
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
            record = row.to_record() if row else None
        return self._resolve_record(record)

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
            record = row.to_record() if row else None
        return self._resolve_record(record)

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
            record = row.to_record() if row else None
        return self._resolve_record(record)

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
            record = row.to_record() if row else None
        return self._resolve_record(record)

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
            records = [r.to_record() for r in rows]
        return [self._resolve_record(r) for r in records]

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
            records = [r.to_record() for r in rows]
        return [self._resolve_record(r) for r in records]

    def list_by_source_bots(
        self,
        source_bot_pks: Sequence[int],
        env: str,
    ) -> List[BotPublishRecord]:
        if not source_bot_pks:
            return []
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(
                    self.Model.source_bot_pk.in_(tuple(source_bot_pks)),
                    self.Model.env == env,
                )
                .order_by(self.Model.gmt_create.desc())
                .all()
            )
            records = [row.to_record() for row in rows]
        return [self._resolve_record(record) for record in records]

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
            records = [r.to_record() for r in rows]
        return [self._resolve_record(r) for r in records]

    def list_lineage_candidates_page(
        self,
        *,
        env: str,
        after_id: int | None,
        limit: int,
    ) -> BotPublishLineagePage:
        """Scan only records whose source Service Bot still exists.

        ``id`` is immutable and monotonically increasing, so this cursor cannot
        skip records when unrelated rows change status while a scan runs.
        """
        if limit < 1 or limit > 1000:
            raise ValueError("lineage page limit is out of range")
        with self._db.orm_session() as db:
            query = (
                db.query(self.Model)
                .join(BotModel, BotModel.id == self.Model.source_bot_pk)
                .filter(
                    self.Model.env == env,
                    BotModel.env == env,
                    BotModel.is_delete == 0,
                    BotModel.bot_type == "service",
                )
            )
            if after_id is not None:
                query = query.filter(self.Model.id > after_id)
            rows = query.order_by(self.Model.id.asc()).limit(limit + 1).all()
            has_more = len(rows) > limit
            visible = rows[:limit]
            records = tuple(row.to_record() for row in visible)
        resolved = tuple(self._resolve_record(record) for record in records)
        if any(record is None for record in resolved):  # pragma: no cover - invariant
            raise RuntimeError("lineage page lost a publish record")
        return BotPublishLineagePage(
            records=resolved,
            next_cursor=(int(visible[-1].id) if has_more and visible else None),
            complete=not has_more,
        )

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
            record = row.to_record() if row else None
        return self._resolve_record(record)

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
            record = row.to_record() if row else None
        return self._resolve_record(record)

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
            record = row.to_record() if row else None
        return self._resolve_record(record)

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
        ext_json, pending = self._offload.prepare(
            ext, publish_id, get_current_env()
        )
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
            # Upload the offloaded artifact only when a row actually took this
            # write (affected > 0): a rejected optimistic-lock update OR a
            # missing publish_id must not write an artifact object that nothing
            # references (it could never be reaped — delete needs the row's env).
            # Inside the txn so a put failure rolls back.
            if affected > 0:
                self._offload.upload(pending)
        if source_status is not None and affected == 0:
            return None
        return self.get_by_id(publish_id)

    def compare_and_set_ext(
        self,
        *,
        publish_id: int,
        expected_ext: Optional[Dict[str, Any]],
        ext: Dict[str, Any],
    ) -> Optional[BotPublishRecord]:
        """Whole-column ext CAS using the exact serialized DB value.

        ``get_by_id`` transparently re-inlines offloaded config artifacts, so both
        the expected and replacement values pass through ``prepare`` to recover
        the canonical representation stored in the TEXT column.
        """
        env = get_current_env()
        expected_json, _ = self._offload.prepare(expected_ext, publish_id, env)
        ext_json, pending = self._offload.prepare(ext, publish_id, env)
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(self.Model.id == publish_id)
            if expected_json is None:
                query = query.filter(self.Model.ext.is_(None))
            else:
                query = query.filter(self.Model.ext == expected_json)
            affected = query.update(
                {
                    self.Model.ext: ext_json,
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
            if affected > 0:
                self._offload.upload(pending)
        if affected == 0:
            return None
        return self.get_by_id(publish_id)

    def compare_and_set_status_with_ext(
        self,
        *,
        publish_id: int,
        source_status: str,
        target_status: str,
        expected_ext: Optional[Dict[str, Any]],
        ext: Dict[str, Any],
    ) -> Optional[BotPublishRecord]:
        """CAS status and the exact serialized ext snapshot in one UPDATE."""
        env = get_current_env()
        expected_json, _ = self._offload.prepare(expected_ext, publish_id, env)
        ext_json, pending = self._offload.prepare(ext, publish_id, env)
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.id == publish_id,
                self.Model.status == source_status,
            )
            if expected_json is None:
                query = query.filter(self.Model.ext.is_(None))
            else:
                query = query.filter(self.Model.ext == expected_json)
            affected = query.update(
                {
                    self.Model.status: target_status,
                    self.Model.ext: ext_json,
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
            if affected > 0:
                self._offload.upload(pending)
        if affected == 0:
            return None
        return self.get_by_id(publish_id)

    def compare_and_set_built_with_ext(
        self,
        *,
        publish_id: int,
        source_status: str,
        target_status: str,
        expected_ext: Optional[Dict[str, Any]],
        ext: Dict[str, Any],
        center_skill_uuids: Sequence[str],
        env: str,
    ) -> Optional[BotPublishRecord]:
        """Fence Offline against the moment an Artifact becomes replayable."""
        expected_json, _ = self._offload.prepare(expected_ext, publish_id, env)
        ext_json, pending = self._offload.prepare(ext, publish_id, env)
        uuids = tuple(sorted(set(center_skill_uuids)))
        with self._db.orm_session() as db:
            skill_ids = [
                int(value[0])
                for value in db.query(Skill.id)
                .filter(Skill.env == env, Skill.skill_uuid.in_(uuids))
                .order_by(Skill.id.asc())
                .all()
            ] if uuids else []
            if len(skill_ids) != len(uuids):
                raise RuntimeError("Artifact references an unknown Center Skill")
            # Acquire one row lock at a time in immutable id order. Offline and
            # every new consumption write take the same row lock.
            for skill_id in skill_ids:
                skill = (
                    db.query(Skill)
                    .filter(Skill.id == skill_id, Skill.env == env)
                    .with_for_update()
                    .one()
                )
                require_skill_online(skill)

            query = db.query(self.Model).filter(
                self.Model.id == publish_id,
                self.Model.status == source_status,
                self.Model.env == env,
            )
            if expected_json is None:
                query = query.filter(self.Model.ext.is_(None))
            else:
                query = query.filter(self.Model.ext == expected_json)
            affected = query.update(
                {
                    self.Model.status: target_status,
                    self.Model.ext: ext_json,
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
            if affected > 0:
                self._offload.upload(pending)
        if affected == 0:
            return None
        return self.get_by_id(publish_id)

    def rollback_flip(
        self,
        *,
        demoted_publish_id: int,
        demoted_ext: Dict[str, Any],
        demoted_from_status: str,
        demoted_to_status: str,
        restored_publish_id: int,
        restored_ext: Dict[str, Any],
        restored_from_status: str,
        restored_to_status: str,
    ) -> tuple[bool, bool]:
        env = get_current_env()
        demoted_json, demoted_pending = self._offload.prepare(
            demoted_ext, demoted_publish_id, env
        )
        restored_json, restored_pending = self._offload.prepare(
            restored_ext, restored_publish_id, env
        )
        with self._db.orm_session() as db:
            demoted_affected = (
                db.query(self.Model)
                .filter(
                    self.Model.id == demoted_publish_id,
                    self.Model.status == demoted_from_status,
                )
                .update(
                    {
                        self.Model.status: demoted_to_status,
                        self.Model.ext: demoted_json,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
            restored_affected = (
                db.query(self.Model)
                .filter(
                    self.Model.id == restored_publish_id,
                    self.Model.status == restored_from_status,
                )
                .update(
                    {
                        self.Model.status: restored_to_status,
                        self.Model.ext: restored_json,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
            # Upload offloaded artifacts only for rows that actually took the write
            # (inside the txn so a put failure rolls both flips back together).
            if demoted_affected > 0:
                self._offload.upload(demoted_pending)
            if restored_affected > 0:
                self._offload.upload(restored_pending)
        return demoted_affected > 0, restored_affected > 0

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

    def _artifact_prefix_of(self, publish_id: int) -> Optional[str]:
        """The object-storage prefix for this record's artifacts, from its
        ``env`` column, or ``None`` if the row is gone.

        Uses the deterministic prefix (not a stored marker), so it reaps EVERY
        offloaded version under the record — the current one plus any superseded
        (content-addressed) leftovers.
        """
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model.env)
                .filter(self.Model.id == publish_id)
                .first()
            )
        if not row or not row[0]:
            return None
        return self._offload.prefix(row[0], publish_id)

    def delete(self, publish_id: int) -> bool:
        # Resolve the record's artifact prefix (if any) before the row is gone,
        # so object storage can be swept after a successful delete. The hard
        # DELETE below stays a single statement (prod parity).
        prefix = self._artifact_prefix_of(publish_id)
        with self._db.orm_session() as db:
            affected = (
                db.query(self.Model)
                .filter(self.Model.id == publish_id)
                .delete(synchronize_session=False)
            )
        if affected > 0 and prefix:
            self._offload.cleanup(prefix)
        return affected > 0
