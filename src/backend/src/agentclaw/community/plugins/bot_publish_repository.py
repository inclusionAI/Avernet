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

import hashlib
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
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

# ── config_artifact offload ─────────────────────────────────────────
# The published ``config_artifact`` (a serialized BotConfigArtifact) rides inside
# the ``ac_bot_publish.ext`` JSON, which is stored in a ``TEXT`` column capped at
# ~64 KB. A richly-configured teclaw bot can serialize past that. When it does,
# the repository writes the artifact's JSON to object storage and replaces it
# inline with a small self-describing marker (:data:`_ARTIFACT_OSS_MARKER`), then
# transparently re-inlines it on read. 60 KB leaves ~4 KB of headroom under the
# 65535-byte TEXT cap for the ext's sibling fields (binding, migration_path, …).
_ARTIFACT_OSS_THRESHOLD_BYTES = 60 * 1024
# The ext key holding the (inline) artifact and, when offloaded, its marker.
_ARTIFACT_KEY = "config_artifact"
_ARTIFACT_OSS_MARKER = "config_artifact_oss"


class BotPublishRepository:
    """Unified ORM ``BotPublishRepositoryProtocol`` implementation."""

    @inject
    def __init__(
        self, db: DatabasePlugin, oss: ObjectStoragePlugin | None = None
    ) -> None:
        self._db = db
        self.Model = BotPublishModel
        self._oss = oss
        # Offloading needs BOTH a write (put_object) and a read (get_object)
        # side. The corp object-storage impl is out-of-tree and may not yet
        # expose get_object; gate on it so a deployment lacking the read method
        # safely stores inline (old behavior) instead of writing an artifact it
        # could never read back. The size fix activates automatically once the
        # impl gains get_object — no code change here.
        self._offload_enabled = oss is not None and callable(
            getattr(oss, "get_object", None)
        )
        if oss is not None and not self._offload_enabled:
            logger.warning(
                "[BotPublishRepository] ObjectStoragePlugin lacks get_object; "
                "config_artifact offload disabled (storing inline)."
            )

    # ── config_artifact offload/inload helpers ──────────────────

    def _artifact_prefix(self, env: str, publish_id: int) -> str:
        """Per-record object-storage prefix. Every offloaded version of one
        publish record lives under here; :meth:`delete` sweeps the whole subtree
        so superseded versions never accumulate."""
        return f"teclaw/{env}/bot_publish/{publish_id}/"

    def _artifact_oss_key(self, env: str, publish_id: int, digest: str) -> str:
        """Content-addressed key for one artifact version.

        The content digest makes each write a NEW immutable object instead of an
        in-place overwrite. That is what stops a rejected optimistic-lock write
        (or a concurrent writer) from clobbering the object a still-valid record
        points at — a marker always names exactly the bytes written with it.
        Superseded versions are reaped by :meth:`delete`'s prefix sweep.
        """
        return (
            f"{self._artifact_prefix(env, publish_id)}"
            f"config_artifact-{digest}.json"
        )

    @staticmethod
    def _strip_stale_marker(ext: Dict[str, Any]) -> Dict[str, Any]:
        """Enforce marker/inline mutual exclusion on write.

        A fresh inline ``config_artifact`` wins over a leftover marker: if both
        are present (e.g. a caller merged a new artifact onto an ext whose marker
        had failed to resolve), drop the marker so we never persist both — which
        would otherwise make the next read fetch stale OSS content over the fresh
        inline artifact.
        """
        if _ARTIFACT_KEY in ext and _ARTIFACT_OSS_MARKER in ext:
            return {k: v for k, v in ext.items() if k != _ARTIFACT_OSS_MARKER}
        return ext

    def _prepare_ext(
        self, ext: Optional[Dict[str, Any]], publish_id: int, env: str
    ) -> tuple[Optional[str], Optional[tuple[str, str]]]:
        """Build the ext JSON to store, plus a pending object-storage upload.

        Returns ``(ext_json, pending)`` where ``pending`` is ``(oss_key,
        artifact_json)`` to write, or ``None``. Performs NO I/O: the caller
        uploads ``pending`` only AFTER confirming the DB write will persist, so a
        rejected optimistic-lock update never writes an orphan or clobbers a live
        object. When ``pending`` is set, ``ext_json`` already carries the marker
        instead of the inline artifact.
        """
        if ext is None:
            return None, None
        ext = self._strip_stale_marker(ext)
        artifact = ext.get(_ARTIFACT_KEY)
        if not (self._offload_enabled and artifact is not None):
            return json.dumps(ext, ensure_ascii=False), None
        artifact_json = json.dumps(artifact, ensure_ascii=False)
        size = len(artifact_json.encode("utf-8"))
        if size <= _ARTIFACT_OSS_THRESHOLD_BYTES:
            return json.dumps(ext, ensure_ascii=False), None
        digest = hashlib.sha1(artifact_json.encode("utf-8")).hexdigest()[:12]
        key = self._artifact_oss_key(env, publish_id, digest)
        ext = {k: v for k, v in ext.items() if k != _ARTIFACT_KEY}
        ext[_ARTIFACT_OSS_MARKER] = {
            "offloaded": True,
            "oss_key": key,
            "size_bytes": size,
            "threshold_bytes": _ARTIFACT_OSS_THRESHOLD_BYTES,
            "note": (
                f"config_artifact ({size} bytes) exceeded the "
                f"{_ARTIFACT_OSS_THRESHOLD_BYTES}-byte inline limit for the "
                "ac_bot_publish.ext TEXT column and was stored in object storage "
                f"at oss_key; the repository re-inlines it as ext['{_ARTIFACT_KEY}'] "
                "on read."
            ),
        }
        return json.dumps(ext, ensure_ascii=False), (key, artifact_json)

    def _upload_pending(self, pending: Optional[tuple[str, str]]) -> None:
        """Write a prepared artifact object. Fail loud on error — better than
        silently truncating the ext column or shipping a dangling marker."""
        if pending is None:
            return
        key, body = pending
        if not self._oss.put_object(key, body):
            raise RuntimeError(
                f"config_artifact offload failed: put_object({key!r}) "
                "returned False"
            )

    def _resolve_ext(
        self, ext: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Inverse of the offload in :meth:`_prepare_ext`.

        If ``ext`` carries the offload marker, fetch the artifact JSON back from
        object storage and re-inline it as ``ext['config_artifact']``, dropping
        the marker so callers see the same shape as an inline artifact. If an
        inline artifact is somehow also present it wins (the marker is dropped
        without a fetch). On a fetch failure, log and leave the marker in place
        (readers already guard on a missing config_artifact) rather than raising
        inside a read path.
        """
        if not ext or _ARTIFACT_OSS_MARKER not in ext:
            return ext
        if _ARTIFACT_KEY in ext:
            return {k: v for k, v in ext.items() if k != _ARTIFACT_OSS_MARKER}
        marker = ext[_ARTIFACT_OSS_MARKER]
        key = marker.get("oss_key") if isinstance(marker, dict) else None
        raw = self._oss.get_object(key) if (self._oss and key) else None
        if raw is None:
            logger.error(
                "[BotPublishRepository] failed to fetch offloaded "
                "config_artifact from object storage: key=%s", key,
            )
            return ext
        resolved = {k: v for k, v in ext.items() if k != _ARTIFACT_OSS_MARKER}
        resolved[_ARTIFACT_KEY] = json.loads(
            raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        )
        return resolved

    def _resolve_record(
        self, record: Optional[BotPublishRecord]
    ) -> Optional[BotPublishRecord]:
        """Re-inline an offloaded artifact on a detached record.

        Called AFTER the DB session closes, so the object-storage network fetch
        never holds a database connection open.
        """
        if record is not None:
            record.ext = self._resolve_ext(record.ext)
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
            ext_json, pending = self._prepare_ext(ext, new_id, env)
            row.ext = ext_json
            self._upload_pending(pending)
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
        ext_json, pending = self._prepare_ext(
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
            # Upload the offloaded artifact only once we know this write took
            # (optimistic lock matched). A rejected update (affected == 0) must
            # NOT touch object storage, or it would clobber the object a still-
            # valid record points at. Inside the txn so a put failure rolls back.
            if not (source_status is not None and affected == 0):
                self._upload_pending(pending)
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

    def _artifact_prefix_of(self, publish_id: int) -> Optional[str]:
        """The object-storage prefix for this record's artifacts, from its
        ``env`` column, or ``None`` if the row is gone.

        Uses the deterministic prefix (not a stored marker), so it reaps EVERY
        offloaded version under the record — the current one plus any superseded
        (content-addressed) or shrunk-back-to-inline leftovers.
        """
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model.env)
                .filter(self.Model.id == publish_id)
                .first()
            )
        if not row or not row[0]:
            return None
        return self._artifact_prefix(row[0], publish_id)

    def delete(self, publish_id: int) -> bool:
        # Resolve the record's artifact prefix (if any) before the row is gone,
        # so object storage can be swept after a successful delete. The hard
        # DELETE below stays a single statement (prod parity); the lookup only
        # runs when offload storage is configured.
        prefix = (
            self._artifact_prefix_of(publish_id)
            if self._oss is not None
            else None
        )
        with self._db.orm_session() as db:
            affected = (
                db.query(self.Model)
                .filter(self.Model.id == publish_id)
                .delete(synchronize_session=False)
            )
        if affected > 0 and prefix:
            # Best effort — a failed OSS cleanup must not fail the DB delete.
            for key in self._oss.list_objects(prefix):
                self._oss.delete_object(key)
        return affected > 0
