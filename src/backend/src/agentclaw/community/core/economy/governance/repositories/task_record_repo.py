"""TaskRecord repository — ``ac_governance_task_record_daily`` read + upsert.

Unified repository for all :class:`GovernanceTaskRecordDaily` operations
(read, write, delete, ticket lifecycle).

Follows the ``DatabasePlugin`` self-managed session pattern
(see ``harness_patch_record_repository``): each method opens its
own ``orm_session()`` — self-managed sessions; env is resolved
Env is resolved internally via ``get_current_env()``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.economy.governance.contracts.models import GovernanceTaskRecordDaily
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


log = get_logger()

# analysis_status values that indicate a completed analysis
_COMPLETED_STATUSES = ("completed", "success", "success_with_warnings")

# Fields that can be updated on existing records (offline upsert)
_UPDATABLE_FIELDS = (
    "bot_id", "bot_name", "governance_decision", "hit_dimensions",
    "hit_dimensions_count", "governance_max_priority", "task_summary",
    "notification_structured",
    "expected_token_saving", "saving_ratio", "analysis_status",
)


def _extract_owner_id(worker_id: str) -> str:
    """Extract owner_id from ``worker_id`` ('{owner_id}:{bot_id}').

    Follows the established convention: single split on first colon.
    """
    return worker_id.split(":", 1)[0]


class TaskRecordRepository:
    """Read + upsert access to ``ac_governance_task_record_daily``."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Read path (self-managed session)
    # ------------------------------------------------------------------

    def get_latest_dt_version(
        self,
    ) -> str | None:
        """Return the max ``dt_version <= today`` from task_record_daily.

        Falls back to yesterday (T+1 convention) if no partition for today.
        Returns ``None`` when no data is available at all (no partition found).
        """
        _env = get_current_env()
        today_str = date.today().strftime("%Y%m%d")
        yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y%m%d")

        with self._db.orm_session() as s:
            s.expire_on_commit = False
            max_dt = s.query(func.max(GovernanceTaskRecordDaily.dt_version)).filter(
                GovernanceTaskRecordDaily.dt_version <= today_str,
                GovernanceTaskRecordDaily.env == _env,
            ).scalar()

            if max_dt is not None:
                return str(max_dt)

            # Fallback: try yesterday (T+1 convention)
            max_dt = s.query(func.max(GovernanceTaskRecordDaily.dt_version)).filter(
                GovernanceTaskRecordDaily.dt_version <= yesterday_str,
                GovernanceTaskRecordDaily.env == _env,
            ).scalar()

        if max_dt is None:
            log.warning(
                "[TaskRecordRepo] No dt_version partition found "
                "(looked up to %s, env=%s). Data may be delayed.",
                yesterday_str, _env,
            )
            return None

        log.info(
            "[TaskRecordRepo] Today partition unavailable, "
            "falling back to dt_version=%s (env=%s)",
            max_dt, _env,
        )
        return str(max_dt)

    def get_actionable_bots(
        self, dt_version: str,
    ) -> list[dict]:
        """Return actionable bots for a given ``dt_version``.

        Filters:
          - ``governance_decision = 'actionable'``
          - ``analysis_status IN ('completed', 'success', 'success_with_warnings')``
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.dt_version == dt_version,
                    GovernanceTaskRecordDaily.governance_decision == "actionable",
                    GovernanceTaskRecordDaily.analysis_status.in_(_COMPLETED_STATUSES),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def get_completed_decisions(
        self, dt_version: str,
    ) -> dict[str, str]:
        """Return the full decision set for a given ``dt_version``.

        UK ``(worker_id, dt_version, env)`` guarantees uniqueness — no GROUP BY needed.
        Returns ``{worker_id: governance_decision}``.

        Note: Do NOT use MAX(governance_decision) for aggregation —
        string lexicographic order (observe > justified > actionable)
        does NOT match business priority (actionable > observe > justified).
        If the UK is ever relaxed, explicit business-priority aggregation
        is required.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(
                    GovernanceTaskRecordDaily.worker_id,
                    GovernanceTaskRecordDaily.governance_decision,
                )
                .filter(
                    GovernanceTaskRecordDaily.dt_version == dt_version,
                    GovernanceTaskRecordDaily.analysis_status.in_(_COMPLETED_STATUSES),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .all()
            )
            return {r.worker_id: r.governance_decision for r in rows}

    def get_max_last_sync_at(
        self,
    ) -> object | None:
        """Return ``MAX(last_sync_at)`` from task_record_daily.

        Used by the scan's data-readiness check: if the value hasn't changed
        since the last scan, the offline pipeline hasn't produced new data yet.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            return s.query(func.max(GovernanceTaskRecordDaily.last_sync_at)).filter(
                GovernanceTaskRecordDaily.env == _env,
            ).scalar()

    # ------------------------------------------------------------------
    # Write path (self-managed session)
    # ------------------------------------------------------------------

    def batch_upsert_task_recs(
        self, records: list[dict],
    ) -> dict:
        """Upsert a batch of task_record_daily rows.

        Logical key: ``(worker_id, dt_version, env)``.
        All records in a single call share the same ``last_sync_at = now()``.

        Args:
            records: List of dicts with task_record_daily fields.
                Accepts both ``dt_version`` and ``dt`` keys (fallback).

        Returns:
            ``{"inserted": N, "updated": N, "errors": N}``
        """
        if not records:
            return {"inserted": 0, "updated": 0, "errors": 0}

        _env = get_current_env()

        # Normalize: offline pipelines may send "dt" instead of "dt_version"
        normalized = self._normalize_dt_field(records)

        now = datetime.now()
        inserted = 0
        updated = 0
        errors = 0

        with self._db.orm_session() as session:
            # Batch-load existing records for dedup (scoped to env)
            worker_ids = {r["worker_id"] for r in normalized if r.get("worker_id")}
            dt_versions = {r["dt_version"] for r in normalized if r.get("dt_version")}

            existing_rows = (
                session.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.worker_id.in_(worker_ids),
                    GovernanceTaskRecordDaily.dt_version.in_(dt_versions),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .all()
            )
            existing_map: dict[tuple[str, str], GovernanceTaskRecordDaily] = {
                (r.worker_id, r.dt_version): r for r in existing_rows
            }

            for rec in normalized:
                try:
                    worker_id = rec.get("worker_id")
                    dt_version = rec.get("dt_version")
                    if not worker_id or not dt_version:
                        log.warning(
                            "[TaskRecordRepo] Skipping record "
                            "missing worker_id or dt_version: %s",
                            rec,
                        )
                        errors += 1
                        continue

                    key = (worker_id, dt_version)
                    gmt_create = self._parse_gmt_create(rec.get("gmt_create"))

                    if key in existing_map:
                        # UPDATE — only overwrite non-None fields
                        row = existing_map[key]
                        for field in _UPDATABLE_FIELDS:
                            val = rec.get(field)
                            if val is not None:
                                setattr(row, field, val)
                        row.last_sync_at = now
                        row.gmt_modified = func.now()
                    else:
                        # INSERT
                        row = GovernanceTaskRecordDaily(
                            worker_id=worker_id,
                            dt_version=dt_version,
                            governance_decision=rec.get("governance_decision"),
                            bot_id=rec.get("bot_id"),
                            owner_id=rec.get("owner_id") or _extract_owner_id(worker_id),
                            bot_name=rec.get("bot_name"),
                            hit_dimensions=rec.get("hit_dimensions"),
                            hit_dimensions_count=rec.get("hit_dimensions_count"),
                            governance_max_priority=rec.get("governance_max_priority"),
                            expected_token_saving=rec.get("expected_token_saving"),
                            saving_ratio=rec.get("saving_ratio"),
                            task_summary=rec.get("task_summary"),
                            notification_structured=rec.get("notification_structured"),
                            analysis_status=rec.get("analysis_status"),
                            last_sync_at=now,
                            gmt_create=gmt_create or now,
                        )
                        # Explicitly set env (ORM default only applies when
                        # the column is not mentioned at all in the constructor).
                        row.env = _env
                        session.add(row)
                        existing_map[key] = row
                        inserted += 1
                        continue  # skip the updated++ below

                    updated += 1

                except Exception:
                    log.exception(
                        "[TaskRecordRepo] Error upserting record: %s",
                        rec,
                    )
                    errors += 1
                    session.rollback()

            try:
                session.commit()
            except Exception:
                log.exception("[TaskRecordRepo] Commit failed")
                # inserted/updated were optimistic counts before commit;
                # rollback means none of them persisted.
                errors = len(normalized)
                inserted = 0
                updated = 0
                session.rollback()

        log.info(
            "[TaskRecordRepo] Batch upsert done: "
            "inserted=%d, updated=%d, errors=%d, total=%d, env=%s",
            inserted, updated, errors, len(normalized), _env,
        )
        return {"inserted": inserted, "updated": updated, "errors": errors}

    # ------------------------------------------------------------------
    # Delete path (admin emergency) — self-managed session
    # ------------------------------------------------------------------

    def count_by_dt_versions(
        self, dt_versions: list[str],
    ) -> dict[str, int]:
        """Count rows per dt_version for the given list (env-scoped)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(
                    GovernanceTaskRecordDaily.dt_version,
                    func.count(),
                )
                .filter(
                    GovernanceTaskRecordDaily.dt_version.in_(dt_versions),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .group_by(GovernanceTaskRecordDaily.dt_version)
                .all()
            )
            return {r[0]: r[1] for r in rows}

    def delete_by_dt_versions(
        self, dt_versions: list[str],
    ) -> int:
        """Delete rows matching dt_versions (env-scoped). Returns deleted count."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            count = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.dt_version.in_(dt_versions),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .delete(synchronize_session="fetch")
            )
            return count

    def delete_by_ids(
        self, ids: list[int],
    ) -> tuple[int, list[int]]:
        """Delete rows by primary key IDs (env-scoped).

        Returns (deleted_count, not_found_ids).
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            existing = (
                s.query(GovernanceTaskRecordDaily.id)
                .filter(
                    GovernanceTaskRecordDaily.id.in_(ids),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .all()
            )
            existing_ids = {r.id for r in existing}
            not_found = [i for i in ids if i not in existing_ids]

            if existing_ids:
                deleted = (
                    s.query(GovernanceTaskRecordDaily)
                    .filter(GovernanceTaskRecordDaily.id.in_(existing_ids))
                    .delete(synchronize_session="fetch")
                )
            else:
                deleted = 0

            return deleted, not_found

    def count_by_ids(
        self, ids: list[int],
    ) -> tuple[int, list[int]]:
        """Count matching rows by primary key IDs (env-scoped), no deletion.

        Returns (match_count, not_found_ids).
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            existing = (
                s.query(GovernanceTaskRecordDaily.id)
                .filter(
                    GovernanceTaskRecordDaily.id.in_(ids),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .all()
            )
            existing_ids = {r.id for r in existing}
            not_found = [i for i in ids if i not in existing_ids]
            return len(existing_ids), not_found

    # ------------------------------------------------------------------
    # Ticket CRUD (task_record as 工单 entity)
    # ------------------------------------------------------------------

    def find_active_ticket(
        self, active_worker: str,
    ) -> dict | None:
        """Find the active ticket for an active_worker (owner_id:bot_id).

        Active = governance_status IN ('open', 'scheduled', 'waiting_review').
        Returns None if no active ticket exists.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.active_worker == active_worker,
                    GovernanceTaskRecordDaily.governance_status.in_(
                        ("open", "scheduled", "waiting_review"),
                    ),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .one_or_none()
            )
            return obj.to_dict() if obj else None

    def find_by_ticket_id(
        self, ticket_id: str,
    ) -> dict | None:
        """Find a ticket by its stable UUID (ticket_id)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.ticket_id == ticket_id,
                    GovernanceTaskRecordDaily.env == _env,
                )
                .one_or_none()
            )
            return obj.to_dict() if obj else None

    def find_latest_closed_by_worker(
        self, worker_id: str,
    ) -> dict | None:
        """Find most recently closed ticket for a worker (cooldown & review_rejected check).

        Ordered by closed_at DESC. Returns None if no closed ticket exists.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.worker_id == worker_id,
                    GovernanceTaskRecordDaily.governance_status == "closed",
                    GovernanceTaskRecordDaily.env == _env,
                )
                .order_by(
                    GovernanceTaskRecordDaily.closed_at.desc(),
                    GovernanceTaskRecordDaily.gmt_modified.desc(),
                )
                .first()
            )
            return obj.to_dict() if obj else None

    def list_active_open_tickets(
        self,
    ) -> list[dict]:
        """List all open tickets with active_worker set (for auto_silence).

        Used by offline-batch to find active open tickets not in current batch.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.governance_status == "open",
                    GovernanceTaskRecordDaily.active_worker.isnot(None),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_scheduled_due(
        self, now: datetime,
    ) -> list[dict]:
        """Find scheduled tickets where mute_until <= now (schedule_due).

        These tickets should transition from scheduled → waiting_review.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.governance_status == "scheduled",
                    GovernanceTaskRecordDaily.mute_until <= now,
                    GovernanceTaskRecordDaily.mute_until.isnot(None),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_auto_silence_eligible(
        self,
        *,
        min_consecutive_days: int,
    ) -> list[dict]:
        """Find open tickets eligible for auto-silence convergence (§7.2.6).

        Conditions: governance_status='open' + latest_decision='normal' +
        consecutive_normal_days >= min_consecutive_days + active_worker set.

        Args:
            min_consecutive_days: ``auto_silence_close_days`` from config.

        Returns:
            List of tickets meeting the convergence threshold.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.governance_status == "open",
                    GovernanceTaskRecordDaily.latest_decision == "normal",
                    GovernanceTaskRecordDaily.consecutive_normal_days
                    >= min_consecutive_days,
                    GovernanceTaskRecordDaily.active_worker.isnot(None),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_remindable_tickets(
        self, now: datetime,
    ) -> list[dict]:
        """Find tickets eligible for reminder creation (§7.3.2).

        Conditions: open + latest_decision=actionable + remind_at <= now
        + remind_at IS NOT NULL + response IS NULL + active_worker set.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.governance_status == "open",
                    GovernanceTaskRecordDaily.latest_decision == "actionable",
                    GovernanceTaskRecordDaily.remind_at <= now,
                    GovernanceTaskRecordDaily.remind_at.isnot(None),
                    GovernanceTaskRecordDaily.response.is_(None),
                    GovernanceTaskRecordDaily.active_worker.isnot(None),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_tickets_by_owner_and_statuses(
        self,
        owner_id: str,
        statuses: list[str],
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        """Owner's tickets in the given statuses, newest first, paged."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.owner_id == owner_id,
                    GovernanceTaskRecordDaily.governance_status.in_(statuses),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .order_by(GovernanceTaskRecordDaily.gmt_create.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in rows]

    def count_active_open(
        self,
    ) -> int:
        """Count all active open tickets (for admin dashboard)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            return (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.governance_status.in_(
                        ("open", "scheduled", "waiting_review"),
                    ),
                    GovernanceTaskRecordDaily.active_worker.isnot(None),
                    GovernanceTaskRecordDaily.env == _env,
                )
                .count()
            )

    def find_ticket_by_notification_id(
        self, notification_id: str,
    ) -> dict | None:
        """Find a ticket via its notify_log's notification_id.

        Used by feedback_service: notification_id → notify_log.ticket_id → task_record.
        """
        from agentclaw.community.core.economy.governance.contracts.models import (
            GovernanceNotifyLog,
        )

        _env = get_current_env()
        with self._db.orm_session() as s:
            notify_row = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.notification_id == notification_id,
                    GovernanceNotifyLog.env == _env,
                )
                .first()
            )
            if notify_row is None or notify_row.ticket_id is None:
                return None
            obj = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.ticket_id == notify_row.ticket_id,
                    GovernanceTaskRecordDaily.env == _env,
                )
                .one_or_none()
            )
            return obj.to_dict() if obj else None

    # ------------------------------------------------------------------
    # Ticket creation (self-managed session)
    # ------------------------------------------------------------------

    def add_ticket(
        self, row: GovernanceTaskRecordDaily,
    ) -> str:
        """Insert a new ticket row (self-managed session).

        Flush ensures subsequent ``find_active_ticket()`` can see this
        newly created ticket.

        Returns the ticket_id of the inserted row.
        """
        with self._db.orm_session() as s:
            s.add(row)
            s.flush()
            return row.ticket_id

    # ------------------------------------------------------------------
    # Test seeding (self-managed session + commit)
    # ------------------------------------------------------------------

    def insert_ticket(self, row: GovernanceTaskRecordDaily) -> None:
        """Insert a full-featured ticket row (self-managed session).

        Unlike ``batch_upsert_task_recs``, this preserves every column
        (ticket_id, governance_status, active_worker, etc.) so endpoint
        tests can seed realistic states without bypassing the repo layer.
        """
        with self._db.orm_session() as session:
            session.add(row)
            session.flush()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_dt_field(records: list[dict]) -> list[dict]:
        """Normalize ``dt`` → ``dt_version`` so dedup logic works uniformly.

        Offline pipelines (CSV / ODPS) typically send ``dt`` as the column
        name, while the ORM model uses ``dt_version``.  Without this step
        the pre-query ``WHERE dt_version IN (...)`` produces an empty set
        and every record is mis-classified as INSERT.
        """
        normalized = []
        for rec in records:
            rec = rec.copy()
            # Prefer explicit dt_version; fall back to dt
            if not rec.get("dt_version") and rec.get("dt"):
                rec["dt_version"] = rec["dt"]
            normalized.append(rec)
        return normalized

    @staticmethod
    def _parse_gmt_create(value: object) -> datetime | None:
        """Parse gmt_create from various formats (offline may send timestamps)."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value)
        if isinstance(value, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
        return None