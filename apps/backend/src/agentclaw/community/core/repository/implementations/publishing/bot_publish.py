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

import json
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence

from injector import inject

from agentclaw.community.kernel.knowledge import KnowledgeError, KnowledgePublicationPolicy
from sqlalchemy import func

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.repository.implementations.publishing import knowledge_build, knowledge_selection, restart_retry
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
        self, db: DatabasePlugin, offload: ConfigArtifactOffloader,
        knowledge_policy: KnowledgePublicationPolicy = KnowledgePublicationPolicy(),
    ) -> None:
        self._db = db
        self.Model = BotPublishModel
        # Offloads an oversized config_artifact out of the ext TEXT column.
        self._offload = offload
        self._knowledge_policy = knowledge_policy

    def _require_observation(self, target_status, observed_workflow_id=None):
        if (self._knowledge_policy.enabled and target_status == PublishStatus.SUCCESS
                and observed_workflow_id is None):
            raise KnowledgeError("KNOWLEDGE_OBSERVATION_REQUIRED")

    @contextmanager
    def _publication_session(self, publish_id=None):
        # Only local DB bookkeeping is in this transaction, never deployment.
        factory = (self._db.transactional_orm_session
                   if self._knowledge_policy.enabled else self._db.orm_session)
        with factory() as db:
            if self._knowledge_policy.enabled and publish_id is not None:
                with knowledge_selection.publication_scope(db, publish_id):
                    yield db
            else:
                yield db

    def _stage_artifact(self, publish_id, ext, source_status=None, **cas):
        """Preflight + immutable PUT, BEFORE any knowledge/skill row locks.

        A concurrent CAS loss can leave an unreferenced immutable object under
        the existing per-record cleanup prefix. An already stale write never PUTs.
        """
        if not self._knowledge_policy.enabled:
            return True
        if "config_artifact" not in ext:
            return True
        with self._db.orm_session() as db:
            row = db.query(self.Model).filter(self.Model.id == publish_id).first()
            if row is None or (source_status is not None and row.status != source_status):
                return False
            if "expected_json" in cas and row.ext != cas["expected_json"]:
                return False
            stored = json.loads(row.ext) if row.ext else {}
            env = get_current_env()
        staged = dict(ext)
        # Validate against the current metadata before uploading, then re-check
        # under the transaction lock to close the preflight/write race.
        staged = knowledge_selection.merge_protected_ext(stored, staged)
        encoded, pending = self._offload.prepare(
            staged, publish_id, env, reserve_bytes=cas.get("reserve_bytes", 0),
        )
        self._validate_knowledge_ext(encoded)
        if pending and stored.get("config_artifact_oss", {}).get("oss_key") != pending[0]:
            self._offload.upload(pending)
            # Delete can race the PUT and sweep its prefix before bytes land.
            # Reap here only if the immutable owner is already gone; a later
            # delete will see the completed PUT and use the native cleanup.
            with self._db.orm_session() as db:
                exists = db.query(self.Model.id).filter(self.Model.id == publish_id).first()
            if exists is None:
                self._offload.cleanup(self._offload.prefix(env, publish_id))
                return False
        return pending[0] if pending else True

    def _upload_native(self, pending, staged):
        if not self._knowledge_policy.enabled:
            self._offload.upload(pending)
        elif pending and pending[0] != staged:
            # A protected-metadata race must roll back, never commit a marker
            # whose bytes weren't staged before acquiring transaction locks.
            raise KnowledgeError("KNOWLEDGE_ARTIFACT_STAGE_CHANGED")

    def _protect_knowledge_ext(self, db, publish_id, ext):
        if self._knowledge_policy.enabled:
            return knowledge_selection.protected_ext(db, publish_id, ext)
        return ext

    def _validate_knowledge_ext(self, ext_json):
        if self._knowledge_policy.enabled:
            knowledge_selection.validate_encoded_ext(ext_json)

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
            self._validate_knowledge_ext(ext_json)
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
                .order_by(self.Model.gmt_create.desc(), self.Model.id.desc())
                .all()
            )
            records = [r.to_record() for r in rows]
        return [self._resolve_record(r) for r in records]

    def get_by_source_bot_and_version(
        self, source_bot_pk: int, version: int, env: str
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.source_bot_pk == source_bot_pk,
                    self.Model.version == version,
                    self.Model.env == env,
                )
                .first()
            )
            record = row.to_record() if row else None
        return self._resolve_record(record)

    def get_latest_by_source_bot(
        self, source_bot_pk: int, env: str
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.source_bot_pk == source_bot_pk,
                    self.Model.env == env,
                )
                .order_by(self.Model.version.desc(), self.Model.id.desc())
                .first()
            )
            record = row.to_record() if row else None
        return self._resolve_record(record)

    def get_latest_by_source_bot_and_status(
        self, source_bot_pk: int, status: str, env: str
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.source_bot_pk == source_bot_pk,
                    self.Model.status == status,
                    self.Model.env == env,
                )
                .order_by(self.Model.version.desc(), self.Model.id.desc())
                .first()
            )
            record = row.to_record() if row else None
        return self._resolve_record(record)

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

    def get_draft_by_source_bot_id(
        self,
        source_bot_id: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.source_bot_id == source_bot_id,
                    self.Model.status == PublishStatus.DRAFT,
                    self.Model.env == env,
                )
                .order_by(self.Model.id.desc())
                .first()
            )
            record = row.to_record() if row else None
        return self._resolve_record(record)

    def get_latest_built_by_source_bot_id(
        self,
        source_bot_id: str,
        env: str,
    ) -> Optional[BotPublishRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.source_bot_id == source_bot_id,
                    self.Model.status.in_([
                        PublishStatus.BUILT.value,
                        PublishStatus.VALIDATING.value,
                    ]),
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
        self._require_observation(target_status)
        if (self._knowledge_policy.enabled and target_status == PublishStatus.BUILDING
                and source_status in (None, PublishStatus.DRAFT)):
            frozen = knowledge_build.freeze_build(self._publication_session, self._offload, publish_id)
            return self.get_by_id(publish_id) if frozen else None
        with self._publication_session() as db:
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
        self._require_observation(target_status)
        if (self._knowledge_policy.enabled and target_status == PublishStatus.BUILDING
                and source_status in (None, PublishStatus.DRAFT)):
            frozen = knowledge_build.freeze_build(
                self._publication_session, self._offload, publish_id, incoming_ext=ext,
            )
            return self.get_by_id(publish_id) if frozen else None
        staged = self._stage_artifact(publish_id, ext, source_status)
        if not staged:
            return None
        with self._publication_session() as db:
            ext = self._protect_knowledge_ext(db, publish_id, ext)
            ext_json, pending = self._offload.prepare(ext, publish_id, get_current_env())
            self._validate_knowledge_ext(ext_json)
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
            # Disabled mode retains native upload-on-write behavior. Opted-in
            # mode has already staged changed immutable bytes before its locks.
            if affected > 0:
                self._upload_native(pending, staged)
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
        staged = self._stage_artifact(publish_id, ext, expected_json=expected_json)
        if not staged:
            return None
        with self._publication_session() as db:
            ext = self._protect_knowledge_ext(db, publish_id, ext)
            ext_json, pending = self._offload.prepare(ext, publish_id, env)
            self._validate_knowledge_ext(ext_json)
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
                self._upload_native(pending, staged)
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
        observed_workflow_id: int | None = None,
    ) -> Optional[BotPublishRecord]:
        """CAS status and the exact serialized ext snapshot in one UPDATE."""
        self._require_observation(target_status, observed_workflow_id)
        env = get_current_env()
        expected_json, _ = self._offload.prepare(expected_ext, publish_id, env)
        if (self._knowledge_policy.enabled and target_status == PublishStatus.BUILDING
                and source_status == PublishStatus.DRAFT):
            frozen = knowledge_build.freeze_build(
                self._publication_session, self._offload, publish_id,
                incoming_ext=ext, expected_json=expected_json, compare_ext=True,
            )
            return self.get_by_id(publish_id) if frozen else None
        online_success = (
            self._knowledge_policy.enabled
            and source_status == PublishStatus.ONLINE_PUB
            and target_status == PublishStatus.SUCCESS
        )
        # Compact completion result: <=256-char Owner (<=1024 UTF-8 bytes),
        # bounded status/reason and native workflow ID. Reserve before locks.
        staged = self._stage_artifact(
            publish_id, ext, source_status, expected_json=expected_json,
            reserve_bytes=2048 if online_success else 0,
        )
        if not staged:
            return None
        with self._publication_session(publish_id if online_success else None) as db:
            online = None
            if online_success:
                online = knowledge_selection.prepare_online(db, publish_id, expected_json, observed_workflow_id)
                if online is None:
                    return None
            ext = self._protect_knowledge_ext(db, publish_id, ext)
            if online is not None:
                ext[knowledge_selection.RESULT_KEY] = online.outcome
            ext_json, pending = self._offload.prepare(ext, publish_id, env)
            self._validate_knowledge_ext(ext_json)
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
                self._upload_native(pending, staged)
                if online is not None:
                    knowledge_selection.replace_online(db, online)
        if affected == 0:
            return None
        return self.get_by_id(publish_id)

    def restore_failed_online_restart(self, *, publish_id, expected_ext):
        """Restricted retry admission; never applies online knowledge effects."""
        env = get_current_env()
        expected_json, _ = self._offload.prepare(expected_ext, publish_id, env)
        ext = {**(expected_ext or {}), "retry": True}
        if not self._knowledge_policy.enabled:
            return self.compare_and_set_status_with_ext(
                publish_id=publish_id, source_status=PublishStatus.FAILED,
                target_status=PublishStatus.SUCCESS, expected_ext=expected_ext, ext=ext,
            )
        staged = self._stage_artifact(
            publish_id, ext, PublishStatus.FAILED, expected_json=expected_json,
        )
        if not staged:
            return None
        with self._publication_session() as db:
            encoded, pending = self._offload.prepare(ext, publish_id, env)
            self._validate_knowledge_ext(encoded)
            restored = restart_retry.restore(db, publish_id, expected_json, encoded)
            if restored:
                self._upload_native(pending, staged)
        return self.get_by_id(publish_id) if restored else None

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
        self._require_observation(target_status)
        expected_json, _ = self._offload.prepare(expected_ext, publish_id, env)
        uuids = tuple(sorted(set(center_skill_uuids)))
        staged = self._stage_artifact(
            publish_id, ext, source_status, expected_json=expected_json,
        )
        if not staged:
            return None
        with self._publication_session() as db:
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

            ext = self._protect_knowledge_ext(db, publish_id, ext)
            ext_json, pending = self._offload.prepare(ext, publish_id, env)
            self._validate_knowledge_ext(ext_json)
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
                self._upload_native(pending, staged)
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
        if self._knowledge_policy.enabled:
            with self._db.orm_session() as db:
                statuses = dict(db.query(self.Model.id, self.Model.status).filter(
                    self.Model.id.in_((demoted_publish_id, restored_publish_id)),
                ).all())
            if (statuses.get(demoted_publish_id) != demoted_from_status
                    or statuses.get(restored_publish_id) != restored_from_status):
                return False, False
        demoted_stage = self._stage_artifact(demoted_publish_id, demoted_ext, demoted_from_status)
        if not demoted_stage:
            return False, False
        restored_stage = self._stage_artifact(restored_publish_id, restored_ext, restored_from_status)
        if not restored_stage:
            return False, False
        with self._publication_session() as db:
            incoming = {demoted_publish_id: demoted_ext, restored_publish_id: restored_ext}
            protected = {
                key: self._protect_knowledge_ext(db, key, incoming[key])
                for key in sorted(incoming)
            }
            demoted_ext = protected[demoted_publish_id]
            restored_ext = protected[restored_publish_id]
            demoted_json, demoted_pending = self._offload.prepare(demoted_ext, demoted_publish_id, env)
            restored_json, restored_pending = self._offload.prepare(restored_ext, restored_publish_id, env)
            self._validate_knowledge_ext(demoted_json)
            self._validate_knowledge_ext(restored_json)
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
            if self._knowledge_policy.enabled and not (demoted_affected and restored_affected):
                # A rejected pair is not a partial success. Roll back even when
                # the other CAS matched, before uploading any offloaded artifact.
                db.rollback()
                return False, False
            # Native disabled path uploads here; opted-in writes staged before locks.
            if demoted_affected > 0:
                self._upload_native(demoted_pending, demoted_stage)
            if restored_affected > 0:
                self._upload_native(restored_pending, restored_stage)
        return demoted_affected > 0, restored_affected > 0

    def restore_draft_knowledge_selection(
        self,
        *,
        draft_publish_id: int,
        source_publish_id: int,
        operation_id: int,
        expected_attempt: int,
        final_result: dict,
    ) -> bool | None:
        """None selects legacy completion; enabled failures never fall back."""
        if not self._knowledge_policy.enabled:
            return None
        with self._publication_session() as db:
            return knowledge_selection.restore_draft(
                db,
                draft_publish_id=draft_publish_id,
                source_publish_id=source_publish_id,
                operation_id=operation_id,
                expected_attempt=expected_attempt,
                final_result=final_result,
            )

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
