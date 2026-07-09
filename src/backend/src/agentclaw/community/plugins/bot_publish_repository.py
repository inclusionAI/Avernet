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

    def _artifact_oss_key(self, env: str, publish_id: int) -> str:
        """Deterministic per-record key. Overwritten on each write (a publish's
        re-stamps reuse the same object), so no orphan copies accumulate."""
        return f"teclaw/{env}/bot_publish/{publish_id}/config_artifact.json"

    def _serialize_ext(
        self, ext: Optional[Dict[str, Any]], publish_id: int, env: str
    ) -> Optional[str]:
        """Serialize ``ext`` to the JSON string stored in the ext column.

        When offloading is enabled and ``ext['config_artifact']`` serializes to
        more than :data:`_ARTIFACT_OSS_THRESHOLD_BYTES`, its JSON is written to
        object storage and replaced with a self-describing marker so the column
        stays small. Raises on an object-storage write failure — a loud failure
        beats silently truncating the ext column.

        Invariant: callers pass a *resolved* ext (a full inline ``config_artifact``
        or none; never a lingering marker), because every read path resolves the
        marker back before handing the record out.
        """
        if ext is None:
            return None
        artifact = ext.get(_ARTIFACT_KEY)
        if self._offload_enabled and artifact is not None:
            artifact_json = json.dumps(artifact, ensure_ascii=False)
            size = len(artifact_json.encode("utf-8"))
            if size > _ARTIFACT_OSS_THRESHOLD_BYTES:
                key = self._artifact_oss_key(env, publish_id)
                if not self._oss.put_object(key, artifact_json):
                    raise RuntimeError(
                        "config_artifact offload failed: "
                        f"put_object({key!r}) returned False (size={size} bytes)"
                    )
                ext = {k: v for k, v in ext.items() if k != _ARTIFACT_KEY}
                ext[_ARTIFACT_OSS_MARKER] = {
                    "offloaded": True,
                    "oss_key": key,
                    "size_bytes": size,
                    "threshold_bytes": _ARTIFACT_OSS_THRESHOLD_BYTES,
                    "note": (
                        f"config_artifact ({size} bytes) exceeded the "
                        f"{_ARTIFACT_OSS_THRESHOLD_BYTES}-byte inline limit for "
                        "the ac_bot_publish.ext TEXT column and was stored in "
                        "object storage at oss_key; the repository re-inlines it "
                        f"as ext['{_ARTIFACT_KEY}'] on read."
                    ),
                }
        return json.dumps(ext, ensure_ascii=False)

    def _resolve_ext(
        self, ext: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Inverse of the offload in :meth:`_serialize_ext`.

        If ``ext`` carries the offload marker, fetch the artifact JSON back from
        object storage and re-inline it as ``ext['config_artifact']``, dropping
        the marker so callers see the same shape as an inline artifact. On a
        fetch failure, log and leave the marker in place (readers already guard
        on a missing config_artifact) rather than raising inside a read path.
        """
        if not ext or _ARTIFACT_OSS_MARKER not in ext:
            return ext
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

    def _to_record(self, row: "BotPublishModel") -> BotPublishRecord:
        """Row → record with any offloaded config_artifact re-inlined."""
        record = row.to_record()
        record.ext = self._resolve_ext(record.ext)
        return record

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
