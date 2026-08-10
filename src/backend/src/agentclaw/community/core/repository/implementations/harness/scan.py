"""Unified HarnessScanRecord repository (prod OceanBase + local SQLite).

One ORM implementation behind the ``HarnessScanRecordRepository``
Protocol. The only per-environment difference is the injected
:class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes, so this single body runs unchanged on
OceanBase (prod) and SQLite (local), collapsing the previous
raw-SQL/ORM twins so CI exercises the prod path too.

Prod-twin parity (the raw-SQL ``ZdasHarnessScanRecordRepository`` is
the reference; the old SQLite twin's divergent behavior is dropped):

- ``create`` / ``batch_create`` are **plain INSERTs** (``db.add`` +
  ``db.flush``, read ``row.id``). The table has **no unique key**, so
  this is never an upsert.
- ``offline_batch`` reproduces the prod twin's emulated upsert: one
  batched SELECT of ``(id, scan_type, scan_dim)`` for the
  ``(bot_id, entity_id)`` set on the *logical* key
  ``bot_id+entity_id+scan_type+scan_dim`` (which is **not** a DB
  constraint), then a per-record branch to an ORM update of the
  matched row or an insert. It is deliberately **not** promoted to a
  DB upsert. The old SQLite twin raised ``NotImplementedError``; this
  is its first real implementation and first SQLite coverage.
- ``update_status`` / ``complete`` / ``update_findings`` /
  ``update_patch_ids`` are **single blind bulk UPDATEs** with no
  SELECT existence guard — prod semantics: 0 rows affected (silent
  no-op) when the id is missing, *not* the old SQLite twin's
  SELECT-first no-op. ``gmt_modified=func.now()`` is set DB-side to
  match prod's ``ON UPDATE CURRENT_TIMESTAMP``. mysqlconnector sets
  CLIENT_FOUND_ROWS so rowcount semantics are identical on both
  backends.
- ``update_patch_ids`` writes **only** ``patch_ids`` and ignores the
  ``findings_with_patch_ids`` argument — exact prod parity. The old
  SQLite twin additionally overwrote ``findings``; that behavior is
  dropped (see the in-method TODO).
- Read methods return the same dict shapes as the twins. Latest-per-
  dim selection uses an ordered scan + first-seen dedup (portable,
  identical result to the prod ``ROW_NUMBER()`` window) and the
  time-window check computes the cutoff in Python (portable,
  equivalent to prod ``DATE_SUB(NOW(), INTERVAL n MINUTE)``).
"""
from __future__ import annotations

import json

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.harness.models import FindingsReport
from agentclaw.community.core.harness.sqlite_models import HarnessScanRecordModel
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.protocols.harness import HarnessScanRecordRepository as HarnessScanRecordRepositoryProtocol

logger = get_logger()


class HarnessScanRecordRepository(
    HarnessScanRecordRepositoryProtocol,
):
    """Unified ORM ``HarnessScanRecordRepository`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self.Model = HarnessScanRecordModel

    @staticmethod
    def _row_to_dict(row: HarnessScanRecordModel) -> dict:
        findings = row.findings
        if isinstance(findings, str):
            findings = json.loads(findings)
        check_items = row.check_items
        if isinstance(check_items, str):
            check_items = json.loads(check_items)
        summary = row.findings_summary
        if isinstance(summary, str):
            summary = json.loads(summary)
        patch_ids = row.patch_ids
        if isinstance(patch_ids, str):
            patch_ids = json.loads(patch_ids)

        return {
            "id": row.id,
            "bot_id": row.bot_id,
            "entity_id": row.entity_id,
            "health_score": row.health_score,
            "score_grade": row.score_grade,
            "check_items": check_items or [],
            "findings": findings or [],
            "findings_summary": summary or {},
            "trigger_source": row.trigger_source,
            "status": row.status,
            "failed_reason": row.failed_reason,
            "env": row.env,
            "scan_dim": row.scan_dim,
            "scan_type": row.scan_type,
            "layer": row.layer,
            "duration_ms": row.duration_ms or 0,
            "patch_ids": patch_ids,
            "bot_publish_id": row.bot_publish_id,
            "gmt_create": row.gmt_create,
            "gmt_modified": row.gmt_modified,
        }

    # ── publish-API methods ────────────────────────────────────

    def batch_create(
        self,
        bot_id: str,
        entity_id: str,
        bot_publish_id: str | None,
        layer: str,
        trigger_source: str,
        records: list[dict],
    ) -> list[int]:
        """Plain INSERT per record (prod parity — no upsert)."""
        with self._db.orm_session() as db:
            ids: list[int] = []
            for rec in records:
                model = self.Model(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    bot_publish_id=bot_publish_id,
                    layer=layer,
                    trigger_source=trigger_source,
                    scan_dim=rec.get("scan_dim"),
                    health_score=rec.get("health_score", 0),
                    score_grade=rec.get("score_grade"),
                    check_items=rec.get("check_items"),
                    findings=rec.get("findings"),
                    findings_summary=rec.get("findings_summary"),
                    duration_ms=rec.get("duration_ms"),
                    scan_type=rec.get("scan_type", "full"),
                    patch_ids=rec.get("patch_ids"),
                    status=rec.get("status", "completed"),
                    failed_reason=rec.get("failed_reason"),
                    env=rec.get("env"),
                )
                db.add(model)
                db.flush()
                ids.append(model.id)
        return ids

    def offline_batch(
        self,
        bot_id: str,
        entity_id: str,
        bot_publish_id: str | None,
        layer: str,
        trigger_source: str,
        records: list[dict],
    ) -> list[dict]:
        """离线T+1批量upsert (prod-twin emulated upsert, ORM).

        Logical key ``bot_id+entity_id+scan_type+scan_dim`` is NOT a DB
        constraint, so this is a SELECT-then-branch emulation — never a
        DB upsert. Mirrors the prod twin statement-for-statement.

        Each ``rec`` may carry an optional ``patches`` list (dev S5):
        after the scan record upsert, the patches are INSERTed into
        ``ac_harness_patch`` and the scan record's ``patch_ids`` is
        rewritten with the new ids; when ``patches`` is empty/absent
        the record's ``patch_ids`` is cleared to ``[]``. Per-record
        result dict gains ``patch_ids`` + ``patches`` keys.
        """
        results: list[dict] = []
        with self._db.orm_session() as db:
            # 1. Batched lookup of existing rows on the logical key.
            scan_types = list(
                {r.get("scan_type") for r in records if r.get("scan_type")}
            )
            scan_dims = list(
                {r.get("scan_dim") for r in records if r.get("scan_dim")}
            )

            existing: dict = {}
            if scan_types and scan_dims:
                rows = (
                    db.query(
                        self.Model.id,
                        self.Model.scan_type,
                        self.Model.scan_dim,
                    )
                    .filter(
                        self.Model.bot_id == bot_id,
                        self.Model.entity_id == entity_id,
                        self.Model.scan_type.in_(scan_types),
                        self.Model.scan_dim.in_(scan_dims),
                    )
                    .all()
                )
                for row in rows:
                    key = (bot_id, entity_id, row[1], row[2])
                    existing[key] = {
                        "id": row[0],
                        "scan_type": row[1],
                        "scan_dim": row[2],
                    }

            # 2. Per-record insert/update decision (+ patches sync).
            for rec in records:
                key = (
                    bot_id,
                    entity_id,
                    rec.get("scan_type"),
                    rec.get("scan_dim"),
                )
                rec_result: dict = {
                    "scan_dim": rec.get("scan_dim", "unknown"),
                    "scan_type": rec.get("scan_type"),
                    "action": None,
                    "id": None,
                    "reason": None,
                    "patch_ids": [],
                    "patches": [],
                }
                try:
                    if key in existing:
                        scan_id = self._do_offline_update(
                            db, existing[key]["id"], entity_id, rec
                        )
                        rec_result["action"] = "updated"
                        rec_result["id"] = scan_id
                    else:
                        scan_id = self._do_offline_insert(
                            db, bot_id, entity_id, bot_publish_id,
                            layer, trigger_source, rec,
                        )
                        existing[key] = {
                            "id": scan_id,
                            "scan_type": rec.get("scan_type"),
                            "scan_dim": rec.get("scan_dim"),
                        }
                        rec_result["action"] = "inserted"
                        rec_result["id"] = scan_id
                except Exception as e:
                    rec_result["action"] = "failed"
                    rec_result["reason"] = str(e)
                    results.append(rec_result)
                    continue  # scan record failed → skip patches

                # 3. Patches sync (dev S5).
                patches = rec.get("patches") or []
                record_env = rec.get("env")
                if patches:
                    success_ids, patch_results = (
                        self._insert_offline_patches(
                            db, scan_id, bot_id, layer,
                            record_env, patches,
                        )
                    )
                    self._update_offline_record_patch_ids(
                        db, scan_id, success_ids
                    )
                    rec_result["patch_ids"] = success_ids
                    rec_result["patches"] = patch_results
                else:
                    # No patches → reset scan record's patch_ids.
                    self._update_offline_record_patch_ids(
                        db, scan_id, []
                    )
                results.append(rec_result)
        return results

    def _insert_offline_patches(
        self,
        db,
        scan_id: int,
        bot_id: str,
        request_layer: str,
        record_env: str | None,
        patches: list[dict],
    ) -> tuple[list[int], list[dict]]:
        """Bulk-insert patches into ``ac_harness_patch`` (DB-assigned
        ids). Returns (success_ids, per-patch result dicts). Faithful
        port of prod's ``_insert_offline_patches``."""
        from agentclaw.community.core.harness.sqlite_models import HarnessPatchModel

        success_ids: list[int] = []
        results: list[dict] = []
        for patch in patches:
            try:
                # Apply prod's default fill-ins on a per-call copy.
                patch.setdefault("is_applied", "N")
                patch["layer"] = (
                    patch.get("layer") or request_layer or "L1"
                )
                patch["scope"] = patch.get("scope") or bot_id
                patch["env"] = (
                    patch.get("env") or record_env or "dev"
                )
                if not patch.get("template_id"):
                    results.append({
                        "source_id": None,
                        "action": "failed",
                        "reason": "missing template_id",
                    })
                    continue
                if not patch.get("content"):
                    results.append({
                        "source_id": None,
                        "action": "failed",
                        "reason": "missing content",
                    })
                    continue
                kwargs = dict(
                    template_id=patch.get("template_id"),
                    record_id=scan_id,
                    name=patch.get("name"),
                    layer=patch.get("layer"),
                    description=patch.get("description"),
                    scope=patch.get("scope"),
                    content=patch.get("content"),
                    is_applied=patch.get("is_applied", "N"),
                    env=patch.get("env"),
                )
                advise = patch.get("advise")
                if advise is not None:
                    kwargs["advise"] = json.dumps(
                        advise, ensure_ascii=False
                    )
                if patch.get("gmt_create"):
                    kwargs["gmt_create"] = patch["gmt_create"]
                if patch.get("gmt_modified"):
                    kwargs["gmt_modified"] = patch["gmt_modified"]
                row = HarnessPatchModel(**kwargs)
                db.add(row)
                db.flush()
                results.append({
                    "id": row.id,
                    "source_id": None,
                    "action": "inserted",
                    "reason": None,
                })
                success_ids.append(row.id)
            except Exception as e:
                results.append({
                    "source_id": None,
                    "action": "failed",
                    "reason": str(e),
                })
        return success_ids, results

    def _update_offline_record_patch_ids(
        self, db, scan_id: int, patch_ids: list[int]
    ) -> None:
        """Write ``patch_ids`` (JSON string) back on the scan record."""
        db.query(self.Model).filter(
            self.Model.id == scan_id
        ).update(
            {self.Model.patch_ids: json.dumps(
                patch_ids, ensure_ascii=False
            )},
            synchronize_session=False,
        )

    def _do_offline_insert(
        self, db, bot_id: str, entity_id: str, bot_publish_id: str | None,
        layer: str, trigger_source: str, rec: dict,
    ) -> int:
        """Insert a record, honoring optional gmt_create/gmt_modified."""
        kwargs = dict(
            bot_id=bot_id,
            entity_id=entity_id,
            bot_publish_id=bot_publish_id,
            layer=layer,
            trigger_source=trigger_source,
            scan_dim=rec.get("scan_dim"),
            health_score=rec.get("health_score", 0),
            score_grade=rec.get("score_grade"),
            # Default JSON-shaped columns when caller passes None
            # (matches dev's prod-twin fix: NOT NULL columns get
            # sensible empty defaults so the INSERT doesn't error).
            check_items=(
                rec.get("check_items")
                if rec.get("check_items") is not None
                else "[]"
            ),
            findings=(
                rec.get("findings")
                if rec.get("findings") is not None
                else "[]"
            ),
            findings_summary=(
                rec.get("findings_summary")
                if rec.get("findings_summary") is not None
                else "{}"
            ),
            duration_ms=rec.get("duration_ms"),
            scan_type=rec.get("scan_type", "full"),
            status=rec.get("status", "completed"),
            failed_reason=rec.get("failed_reason"),
            env=rec.get("env", "dev"),
        )
        if rec.get("gmt_create"):
            kwargs["gmt_create"] = rec["gmt_create"]
        if rec.get("gmt_modified"):
            kwargs["gmt_modified"] = rec["gmt_modified"]
        model = self.Model(**kwargs)
        db.add(model)
        db.flush()
        return model.id

    def _do_offline_update(
        self, db, row_id: int, entity_id: str, rec: dict
    ) -> int:
        """Update a record (always sets entity_id), honoring optional
        gmt_modified — only fields present in ``rec`` are written,
        matching the prod twin."""
        # gmt_modified=func.now() DB-side matches prod's
        # ON UPDATE CURRENT_TIMESTAMP (a Core/bulk UPDATE doesn't fire
        # the ORM onupdate, and SQLite has no ON UPDATE); an explicit
        # rec["gmt_modified"] still overrides, as in the prod twin.
        values: dict = {
            self.Model.entity_id: entity_id,
            self.Model.gmt_modified: func.now(),
        }
        update_fields = [
            "health_score", "score_grade", "check_items", "findings",
            "findings_summary", "duration_ms", "status", "failed_reason",
            "env",
        ]
        for f in update_fields:
            # Skip when the caller explicitly passed None (dev's prod
            # fix — preserves the existing value rather than nulling).
            if f in rec and rec[f] is not None:
                values[getattr(self.Model, f)] = rec[f]
        if rec.get("gmt_modified"):
            values[self.Model.gmt_modified] = rec["gmt_modified"]
        db.query(self.Model).filter(self.Model.id == row_id).update(
            values, synchronize_session=False
        )
        return row_id

    def get_latest_dim_records(
        self,
        bot_id: str,
        entity_id: str,
        bot_publish_id: str | None = None,
        match_null_publish: bool = True,
    ) -> list[dict]:
        """Latest scan record per scan_dim (any status).

        When ``bot_publish_id`` is None:
        - ``match_null_publish=True`` (default): only records with
          ``bot_publish_id IS NULL`` are matched (prod parity).
        - ``match_null_publish=False``: records with any
          ``bot_publish_id`` value are returned.

        Ordered scan + first-seen dedup — portable and identical in
        result to the prod twin's ``ROW_NUMBER() OVER (PARTITION BY
        COALESCE(scan_dim,'') ORDER BY gmt_create DESC)``.
        """
        env = get_current_env()
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.entity_id == entity_id,
                self.Model.env == env,
            )
            if bot_publish_id is not None:
                query = query.filter(
                    self.Model.bot_publish_id == bot_publish_id
                )
            elif match_null_publish:
                query = query.filter(self.Model.bot_publish_id.is_(None))

            rows = query.order_by(
                self.Model.gmt_create.desc(), self.Model.id.desc()
            ).all()

            seen: set[str] = set()
            result: list[dict] = []
            for row in rows:
                dim = row.scan_dim
                dim_key = dim if dim is not None else ""
                if dim_key in seen:
                    continue
                seen.add(dim_key)
                result.append({
                    "scan_dim": row.scan_dim,
                    "health_score": row.health_score,
                    "grade": row.score_grade,
                    "check_items": row.check_items,
                    "findings": row.findings,
                    "findings_summary": row.findings_summary,
                    "trigger_source": row.trigger_source,
                    "status": row.status,
                    "failed_reason": row.failed_reason,
                    "env": row.env,
                    "duration_ms": row.duration_ms,
                    "scan_type": row.scan_type,
                    "patch_ids": row.patch_ids,
                    "gmt_create": (
                        row.gmt_create.isoformat()
                        if row.gmt_create else None
                    ),
                })
            return result

    def list_dim_history(
        self,
        bot_id: str,
        entity_id: str,
        scan_dim: str | None = None,
        bot_publish_id: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict], int]:
        """Paginated scan history per dimension for a bot (dev S5).

        Optional filters: ``scan_dim``, ``bot_publish_id``. Ordered by
        ``gmt_create DESC``. Returns ``(items, total)``.
        """
        env = get_current_env()
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.entity_id == entity_id,
                self.Model.env == env,
            )
            if scan_dim is not None:
                query = query.filter(self.Model.scan_dim == scan_dim)
            if bot_publish_id is not None:
                query = query.filter(
                    self.Model.bot_publish_id == bot_publish_id
                )
            total = query.count()
            offset = (page - 1) * size
            rows = (
                query.order_by(self.Model.gmt_create.desc())
                .offset(offset)
                .limit(size)
                .all()
            )
            items: list[dict] = []
            for row in rows:
                items.append({
                    "id": row.id,
                    "bot_id": row.bot_id,
                    "entity_id": row.entity_id,
                    "health_score": row.health_score,
                    "grade": row.score_grade,
                    "check_items": row.check_items,
                    "findings": row.findings,
                    "findings_summary": row.findings_summary,
                    "trigger_source": row.trigger_source,
                    "status": row.status,
                    "failed_reason": row.failed_reason,
                    "env": row.env,
                    "gmt_create": (
                        row.gmt_create.isoformat()
                        if row.gmt_create else None
                    ),
                    "gmt_modified": (
                        row.gmt_modified.isoformat()
                        if row.gmt_modified else None
                    ),
                    "scan_dim": row.scan_dim,
                    "scan_type": row.scan_type,
                    "duration_ms": row.duration_ms,
                    "patch_ids": row.patch_ids,
                    "bot_publish_id": row.bot_publish_id,
                })
            return items, total

    # ── diagnose-API methods ──────────────────────────────────

    def create(self, report: FindingsReport) -> int:
        """Plain INSERT (prod parity — no upsert)."""
        from agentclaw.community.core.harness.models import serialize_findings_grouped

        findings_json = serialize_findings_grouped(report.findings)
        layer_val = (
            report.layer.value
            if hasattr(report.layer, "value")
            else str(report.layer)
        )
        scan_dim = f"{report.scan_type}:{layer_val}"

        with self._db.orm_session() as db:
            row = self.Model(
                bot_id=str(report.bot_id),
                entity_id=str(report.entity_id),
                health_score=report.health_score,
                score_grade=report.score_grade,
                check_items=json.dumps(report.check_items, default=str),
                findings=findings_json,
                findings_summary=json.dumps(
                    report.findings_summary, default=str
                ),
                trigger_source=report.trigger_source,
                status=report.status,
                failed_reason=report.failed_reason,
                env=report.env,
                scan_dim=scan_dim,
                scan_type=report.scan_type,
                duration_ms=report.duration_ms,
                layer=layer_val,
                bot_publish_id=report.bot_publish_id,
            )
            db.add(row)
            db.flush()
            return row.id

    def get_by_id(self, scan_id: int) -> dict | None:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.id == scan_id)
                .first()
            )
            return self._row_to_dict(row) if row else None

    def get_recent(
        self,
        bot_id: str,
        entity_id: str,
        scan_type: str | None = None,
        layer: str | None = None,
    ) -> dict | None:
        env = get_current_env()
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.entity_id == entity_id,
                self.Model.env == env,
                self.Model.status == "completed",
            )
            if scan_type:
                query = query.filter(self.Model.scan_type == scan_type)
            if layer:
                query = query.filter(self.Model.layer == layer)
            row = query.order_by(self.Model.gmt_create.desc()).first()
            return self._row_to_dict(row) if row else None

    def list_records(
        self,
        bot_id: str,
        entity_id: str,
        page: int = 1,
        size: int = 20,
        scan_type: str | None = None,
        layer: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict], int]:
        env = get_current_env()
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.entity_id == entity_id,
                self.Model.env == env,
            )
            if scan_type:
                query = query.filter(self.Model.scan_type == scan_type)
            if layer:
                query = query.filter(self.Model.layer == layer)
            if status:
                query = query.filter(self.Model.status == status)

            total = query.count()
            offset = (page - 1) * size
            rows = (
                query.order_by(self.Model.gmt_create.desc())
                .offset(offset)
                .limit(size)
                .all()
            )
            return [self._row_to_dict(r) for r in rows], total

    def update_status(
        self,
        scan_id: int,
        status: str,
        failed_reason: str | None = None,
    ) -> None:
        # Single blind UPDATE — prod parity (no SELECT guard; 0 rows
        # when id missing). gmt_modified set DB-side to match prod's
        # ON UPDATE CURRENT_TIMESTAMP.
        values: dict = {
            self.Model.status: status,
            self.Model.gmt_modified: func.now(),
        }
        if failed_reason:
            values[self.Model.failed_reason] = failed_reason
        with self._db.orm_session() as db:
            db.query(self.Model).filter(
                self.Model.id == scan_id
            ).update(values, synchronize_session=False)

    def complete(self, scan_id: int, report: FindingsReport) -> None:
        from agentclaw.community.core.harness.models import serialize_findings_grouped

        findings_json = serialize_findings_grouped(report.findings)
        layer_val = (
            report.layer.value
            if hasattr(report.layer, "value")
            else str(report.layer)
        )
        values = {
            self.Model.health_score: report.health_score,
            self.Model.score_grade: report.score_grade,
            self.Model.check_items: json.dumps(
                report.check_items, default=str
            ),
            self.Model.findings: findings_json,
            self.Model.findings_summary: json.dumps(
                report.findings_summary, default=str
            ),
            self.Model.status: report.status,
            self.Model.failed_reason: report.failed_reason,
            self.Model.duration_ms: report.duration_ms,
            self.Model.scan_dim: f"{report.scan_type}:{layer_val}",
            self.Model.bot_publish_id: report.bot_publish_id,
            # gmt_modified set DB-side to match prod's ON UPDATE
            # CURRENT_TIMESTAMP: the prod twin's raw UPDATE omits this
            # column and the DB advances it; a Core/bulk UPDATE here
            # fires neither prod's ON UPDATE (SQLite) nor the ORM
            # onupdate, so we set func.now() for cross-backend parity.
            self.Model.gmt_modified: func.now(),
        }
        with self._db.orm_session() as db:
            db.query(self.Model).filter(
                self.Model.id == scan_id
            ).update(values, synchronize_session=False)

    def update_findings(
        self,
        scan_id: int,
        findings_json: str,
        findings_summary_json: str,
        check_items_json: str,
        health_score: int,
        score_grade: str,
    ) -> None:
        values = {
            self.Model.findings: findings_json,
            self.Model.findings_summary: findings_summary_json,
            self.Model.check_items: check_items_json,
            self.Model.health_score: health_score,
            self.Model.score_grade: score_grade,
            # gmt_modified set DB-side to match prod's ON UPDATE
            # CURRENT_TIMESTAMP: the prod twin's raw UPDATE omits this
            # column and the DB advances it; a Core/bulk UPDATE here
            # fires neither prod's ON UPDATE (SQLite) nor the ORM
            # onupdate, so we set func.now() for cross-backend parity.
            self.Model.gmt_modified: func.now(),
        }
        with self._db.orm_session() as db:
            db.query(self.Model).filter(
                self.Model.id == scan_id
            ).update(values, synchronize_session=False)

    def update_patch_ids(
        self,
        scan_id: int,
        patch_ids: list[int],
        findings_with_patch_ids: str | None = None,
    ) -> None:
        # TODO(repo-unify): findings_with_patch_ids is dead — the prod
        # twin never wrote the findings column in this method; only the
        # old SQLite twin did. Prod parity = ignore it. The Protocol
        # param is retained (no signature change this session); promote
        # or remove the param in a later cleanup.
        values = {
            self.Model.patch_ids: json.dumps(patch_ids),
            # gmt_modified set DB-side to match prod's ON UPDATE
            # CURRENT_TIMESTAMP: the prod twin's raw UPDATE omits this
            # column and the DB advances it; a Core/bulk UPDATE here
            # fires neither prod's ON UPDATE (SQLite) nor the ORM
            # onupdate, so we set func.now() for cross-backend parity.
            self.Model.gmt_modified: func.now(),
        }
        with self._db.orm_session() as db:
            db.query(self.Model).filter(
                self.Model.id == scan_id
            ).update(values, synchronize_session=False)

    def has_active_scan(
        self,
        bot_id: str,
        entity_id: str,
        within_minutes: int = 5,
        bot_publish_id: str | None = None,
    ) -> bool:
        """True if an active scan was created within the last N minutes.

        Python-computed cutoff — portable, equivalent to the prod
        twin's ``DATE_SUB(NOW(), INTERVAL n MINUTE)``.
        """
        from sqlalchemy import text

        active_statuses = ("scanning", "scan_completed", "patching")

        # Use DB-native time expression for cutoff (same timezone as gmt_create)
        # Works on SQLite, MySQL, and OceanBase without Python timezone issues
        with self._db.orm_session() as db:
            # Build cutoff expression using DB's native time function
            # SQLite: datetime('now', '-N minutes')
            # MySQL/OceanBase: NOW() - INTERVAL N MINUTE
            driver_name = db.bind.url.drivername if db.bind else ""

            if "sqlite" in driver_name:
                # SQLite: datetime('now', '-5 minutes') returns local time by default
                cutoff_expr = text(f"datetime('now', '-{within_minutes} minutes')")
            else:
                # MySQL/OceanBase: NOW() - INTERVAL 5 MINUTE
                cutoff_expr = text(f"NOW() - INTERVAL {within_minutes} MINUTE")

            query = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.entity_id == entity_id,
                self.Model.status.in_(active_statuses),
                self.Model.gmt_create >= cutoff_expr,
            )
            if bot_publish_id is not None:
                query = query.filter(
                    self.Model.bot_publish_id == bot_publish_id
                )
            else:
                query = query.filter(self.Model.bot_publish_id.is_(None))
            return query.first() is not None
