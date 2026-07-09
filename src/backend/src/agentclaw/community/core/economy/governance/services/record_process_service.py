"""Record process and offline-batch service for governance task_record / notify_log.

Implements:
  - :meth:`process_record` — §7.1.4 Steps 1–6: single record processing
    (whitelist filter, active ticket check, snapshot refresh, cooldown check,
    new ticket + first_send notify creation).
  - :meth:`process_offline_batch` — §7.2: batch ingest via process_record
    + data quality validation.

Recovery detection (auto-silence) is the responsibility of scan_service's
daily ``consecutive_normal_days`` tracking, NOT the batch ingestion path.

All data access delegates to repositories; this service owns orchestration,
business rules, and ticket lifecycle transitions only.

Repos use self-managed sessions.
Ticket mutations (refresh, whitelist-close) are performed in a dedicated
``self._db.orm_session()`` within the service.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.core.economy.governance.contracts.enums import AuditAction
from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceNotifyLog,
    GovernanceTaskRecordDaily,
)
from agentclaw.community.core.economy.governance.services.notify_builder_service import (
    render_governance_notify,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.repositories.audit_repo import (
        GovernanceAuditRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
        NotifyLogRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
        TaskRecordRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
        GovernanceWhitelistRepository,
    )

log = logging.getLogger(__name__)


# ── Result types ─────────────────────────────────────────────────────────


@dataclass
class RecordProcessResult:
    """Result of processing a single record through process_record."""

    worker_key: str
    entered_governance_scope: bool = False
    action: str = ""  # enqueued / still_actionable / whitelist_filtered / cooldown_filtered / whitelist_closed
    reason: str = ""
    ticket_id: str | None = None
    notification_md_preview: str | None = None


@dataclass
class OfflineBatchResult:
    """Aggregate result of an offline-batch run."""

    batch_id: str = ""
    run_id: str = ""
    total_records: int = 0
    upsert_results: list[RecordProcessResult] = field(default_factory=list)
    batch_quality_skipped: bool = False
    batch_quality_skip_reasons: list[str] = field(default_factory=list)
    errors: int = 0


# ── Service ──────────────────────────────────────────────────────────────


class GovernanceRecordService:
    """Single-record process and offline-batch processing.

    Follows §7.1.4 (process_record) and §7.2 (process_offline_batch)
    of the design specification.

    Note: Phase 3 auto-silence diff (batch-level worker diff) has been
    removed.  Recovery detection is handled by scan_service's daily
    ``consecutive_normal_days`` tracking, which has full data visibility
    and does not suffer from partial-batch false positives.
    """

    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        task_repo: TaskRecordRepository,
        whitelist_repo: GovernanceWhitelistRepository,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        config: Any,  # EconomyGovernanceConfig
    ) -> None:
        self._db = db
        self._task_repo = task_repo
        self._whitelist_repo = whitelist_repo
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._config = config

    # ------------------------------------------------------------------
    # Public: process_record (§7.1.4)
    # ------------------------------------------------------------------

    def process_record(
        self,
        record: dict,
        *,
        run_id: str,
        dry_run: bool = False,
        notify_source: str = "offline_batch",
    ) -> RecordProcessResult:
        """Process a single governance record (§7.1.4 Steps 1–6).

        Args:
            record: Dict with keys: owner_id, bot_id, bot_name,
                governance_decision, dt_version, hit_dimensions,
                hit_dimensions_count, governance_max_priority,
                expected_token_saving, saving_ratio, task_summary,
                notification_structured, analysis_status.
            run_id: Correlation ID for audit trail.
            dry_run: True → no writes, return preview only.
            notify_source: ``offline_batch`` / ``online_cron`` / ``manual`` — written
                to ``notify_log.notify_source`` on first_send creation.

        Returns:
            RecordProcessResult with action taken and optional preview.
        """
        # Step 1: Resolve worker_key (§7.1.4 Step 1 + §5.4 validation)
        # Prefer explicit worker_id from producer (CSV/ODPS) to avoid
        # reconstruction mismatch; fall back to owner_id:bot_id.
        worker_key = record.get("worker_id", "")
        if not worker_key or ":" not in worker_key:
            owner_id = record.get("owner_id", "")
            bot_id = record.get("bot_id", "")
            worker_key = f"{owner_id}:{bot_id}"
        else:
            # Extract owner_id / bot_id from worker_id for downstream use
            owner_id, bot_id = worker_key.split(":", 1)

        validation_error = self._validate_worker_key(worker_key)
        if validation_error:
            return RecordProcessResult(
                worker_key=worker_key,
                action="invalid",
                reason=validation_error,
            )

        dt_version = record.get("dt_version", "")

        # Step 2: Whitelist filter (§7.1.4 Step 2) — self-managed session
        whitelist_set = self._whitelist_repo.get_whitelist_set()
        is_whitelisted = (bot_id, owner_id) in whitelist_set

        # Step 3: Find active ticket (self-managed session)
        active_ticket = self._task_repo.find_active_ticket(worker_key)

        if is_whitelisted:
            return self._handle_whitelist_hit(
                active_ticket=active_ticket,
                worker_key=worker_key,
                owner_id=owner_id,
                bot_id=bot_id,
                dt_version=dt_version,
                run_id=run_id,
                dry_run=dry_run,
            )

        # Step 4: Active ticket exists → refresh snapshot (§7.1.4 Step 4)
        if active_ticket is not None:
            return self._handle_active_ticket_refresh(
                ticket=active_ticket,
                record=record,
                worker_key=worker_key,
                owner_id=owner_id,
                bot_id=bot_id,
                dt_version=dt_version,
                run_id=run_id,
                dry_run=dry_run,
            )

        # Step 5: No active ticket → cooldown check (§7.1.4 Step 5)
        latest_closed = self._task_repo.find_latest_closed_by_worker(
            worker_key,
        )
        now = datetime.now()
        if (
            latest_closed is not None
            and latest_closed.get("cooldown_until") is not None
            and latest_closed.get("cooldown_until") > now
        ):
            # Cooldown active → skip
            if not dry_run:
                self._audit_repo.add_audit(
                    run_id, bot_id, owner_id,
                    check_result="actionable",
                    governance_decision=record.get("governance_decision"),
                    hit_dimensions=record.get("hit_dimensions"),
                    action_taken=AuditAction.COOLDOWN_FILTERED,
                    dry_run=0,
                )
            return RecordProcessResult(
                worker_key=worker_key,
                entered_governance_scope=False,
                action="cooldown_filtered",
                reason=f"cooldown_until={latest_closed.get('cooldown_until').isoformat()}",
                ticket_id=latest_closed.get("ticket_id"),
            )

        # Step 6: Create new ticket + first_send notify (§7.1.4 Step 6)
        return self._create_new_ticket(
            record=record,
            worker_key=worker_key,
            owner_id=owner_id,
            bot_id=bot_id,
            dt_version=dt_version,
            run_id=run_id,
            dry_run=dry_run,
            notify_source=notify_source,
            latest_closed=latest_closed,
        )

    # ------------------------------------------------------------------
    # Public: process_offline_batch (§7.2)
    # ------------------------------------------------------------------

    def process_offline_batch(
        self,
        records: list[dict],
        *,
        batch_id: str,
        dt_version: str,
        total_count: int,
        dry_run: bool = False,
    ) -> OfflineBatchResult:
        """Execute offline-batch processing (§7.2).

        Phase 1 (ingest): run process_record per record.
        Phase 2 (data quality validation): best-effort quality check.

        Recovery detection (auto-silence) is NOT performed here — it is
        the responsibility of scan_service's daily ``consecutive_normal_days``
        tracking.  Batch-level worker diff was removed because partial-batch
        uploads would falsely silence tickets from other batches.

        Args:
            records: List of governance record dicts.
            batch_id: Batch unique ID for audit.
            dt_version: Offline data version.
            total_count: Expected record count (for quality check).
            dry_run: True → no writes.

        Returns:
            OfflineBatchResult with upsert outcomes.
        """
        run_id = batch_id or uuid.uuid4().hex
        result = OfflineBatchResult(
            batch_id=batch_id,
            run_id=run_id,
            total_records=len(records),
        )

        # Deduplicate by worker_key to prevent UNIQUE constraint failures
        # when the same worker appears multiple times in a single batch.
        deduped_records = self._deduplicate_by_worker(records)

        # Phase 1: Ingest — process_record per record (§7.2 Step 1)
        # Repos self-manage sessions; no outer orm_session needed.
        for rec in deduped_records:
            try:
                upsert_result = self.process_record(
                    record=rec,
                    run_id=run_id,
                    dry_run=dry_run,
                    notify_source="offline_batch",
                )
                result.upsert_results.append(upsert_result)
            except Exception:
                log.exception(
                    "[OfflineBatch] Error processing record worker_key=%s",
                    rec.get("worker_id", rec.get("owner_id", "")),
                )
                result.errors += 1

        # Phase 2: Data quality validation (§7.2.4)
        skip_reasons = self._validate_batch_quality(
            records=records,
            total_count=total_count,
        )

        if skip_reasons:
            result.batch_quality_skipped = True
            result.batch_quality_skip_reasons = skip_reasons
            # Write quality-skip audit (self-managed session)
            self._audit_repo.add_audit(
                run_id,
                action_taken=AuditAction.BATCH_QUALITY_SKIP_SILENCE,
                source="offline_batch",
                error_msg="; ".join(skip_reasons),
                dry_run=1 if dry_run else 0,
            )

        if dry_run:
            log.info(
                "[OfflineBatch] dry_run=True — run_id=%s",
                run_id,
            )
            return result

        log.info(
            "[OfflineBatch] Completed: batch_id=%s, records=%d, errors=%d",
            batch_id, len(records),
            result.errors,
        )
        return result

    # ------------------------------------------------------------------
    # Internal: Whitelist handling (§7.1.4 Step 2)
    # ------------------------------------------------------------------

    def _handle_whitelist_hit(
        self,
        *,
        active_ticket: dict | None,
        worker_key: str,
        owner_id: str,
        bot_id: str,
        dt_version: str,
        run_id: str,
        dry_run: bool,
    ) -> RecordProcessResult:
        """Process whitelist-hit cases (§7.1.4 Step 2).

        - No active ticket → audit whitelist_filtered, skip.
        - Active ticket exists → close ticket (whitelist_filtered, §7.2.7),
          cancel pending notify.
        """
        now = datetime.now()

        if active_ticket is None:
            # Whitelist hit, no active ticket → only audit
            if not dry_run:
                self._audit_repo.add_audit(
                    run_id, bot_id, owner_id,
                    check_result="actionable",
                    action_taken=AuditAction.SCAN_SKIP_WHITELIST,
                    dry_run=0,
                )
            return RecordProcessResult(
                worker_key=worker_key,
                entered_governance_scope=False,
                action="whitelist_filtered",
                reason="whitelist_hit_no_active_ticket",
            )

        # Whitelist hit + active ticket → close ticket (§7.2.7)
        if not dry_run:
            # Close ticket via dedicated session
            with self._db.orm_session() as s:
                db_ticket = (
                    s.query(GovernanceTaskRecordDaily)
                    .filter(
                        GovernanceTaskRecordDaily.ticket_id == active_ticket.get("ticket_id"),
                    )
                    .one_or_none()
                )
                if db_ticket is not None:
                    db_ticket.governance_status = "closed"
                    db_ticket.close_reason = "whitelist_filtered"
                    db_ticket.closed_at = now
                    db_ticket.active_worker = None
                    db_ticket.last_sync_at = now

            # Cancel pending notify (self-managed session)
            if active_ticket.get("ticket_id"):
                self._notify_repo.cancel_pending_by_ticket(
                    active_ticket.get("ticket_id"),
                )

            self._audit_repo.add_audit(
                run_id, bot_id, owner_id,
                action_taken=AuditAction.WHITELIST_CLOSED,
                dry_run=0,
            )

        return RecordProcessResult(
            worker_key=worker_key,
            entered_governance_scope=False,
            action="whitelist_closed",
            reason="whitelist_hit_active_ticket_closed",
            ticket_id=active_ticket.get("ticket_id"),
        )

    # ------------------------------------------------------------------
    # Internal: Active ticket refresh (§7.1.4 Step 4)
    # ------------------------------------------------------------------

    def _handle_active_ticket_refresh(
        self,
        *,
        ticket: dict,
        record: dict,
        worker_key: str,
        owner_id: str,
        bot_id: str,
        dt_version: str,
        run_id: str,
        dry_run: bool,
    ) -> RecordProcessResult:
        """Refresh snapshot on existing active ticket (§7.1.4 Step 4).

        Guard: only refresh when the incoming ``dt_version`` is strictly
        newer than the ticket's current ``dt_version``.  A stale (older or
        equal) dt_version means the record carries outdated data — skip
        refresh to prevent regression.
        """
        now = datetime.now()
        prev_latest_decision = ticket.get("latest_decision")

        # Guard: reject stale dt_version (older or equal → skip refresh)
        existing_dt = ticket.get("dt_version") or ""
        if dt_version and existing_dt and dt_version <= existing_dt:
            log.debug(
                "[record-process] Skip refresh: incoming dt_version=%s "
                "<= existing dt_version=%s, worker_key=%s",
                dt_version, existing_dt, worker_key,
            )
            self._audit_repo.add_audit(
                run_id, bot_id, owner_id,
                check_result="actionable",
                governance_decision=record.get("governance_decision"),
                hit_dimensions=record.get("hit_dimensions"),
                action_taken=AuditAction.STILL_ACTIONABLE,
                dry_run=0,
            )
            return RecordProcessResult(
                worker_key=worker_key,
                entered_governance_scope=True,
                action="still_actionable",
                reason="stale_dt_version_skipped",
                ticket_id=ticket.get("ticket_id"),
            )

        if not dry_run:
            # Refresh snapshot fields via dedicated session
            incoming_decision = record.get("governance_decision", "actionable")
            is_still_actionable = incoming_decision == "actionable"

            with self._db.orm_session() as s:
                db_ticket = (
                    s.query(GovernanceTaskRecordDaily)
                    .filter(
                        GovernanceTaskRecordDaily.ticket_id == ticket.get("ticket_id"),
                    )
                    .one_or_none()
                )
                if db_ticket is None:
                    return RecordProcessResult(
                        worker_key=worker_key,
                        action="error",
                        reason="ticket_disappeared_between_read_and_write",
                    )

                db_ticket.dt_version = dt_version
                db_ticket.bot_name = record.get("bot_name", db_ticket.bot_name)
                db_ticket.hit_dimensions = record.get("hit_dimensions", db_ticket.hit_dimensions)
                db_ticket.hit_dimensions_count = record.get(
                    "hit_dimensions_count", db_ticket.hit_dimensions_count,
                )
                db_ticket.governance_max_priority = record.get(
                    "governance_max_priority", db_ticket.governance_max_priority,
                )
                db_ticket.expected_token_saving = record.get(
                    "expected_token_saving", db_ticket.expected_token_saving,
                )
                db_ticket.saving_ratio = record.get("saving_ratio", db_ticket.saving_ratio)
                db_ticket.task_summary = record.get("task_summary", db_ticket.task_summary)
                db_ticket.notification_structured = record.get(
                    "notification_structured", db_ticket.notification_structured,
                )
                db_ticket.analysis_status = record.get(
                    "analysis_status", db_ticket.analysis_status,
                )
                db_ticket.last_seen_at = now
                db_ticket.last_sync_at = now
                db_ticket.last_decision_dt_version = dt_version

                if is_still_actionable:
                    db_ticket.latest_decision = "actionable"
                    db_ticket.consecutive_normal_days = 0
                else:
                    db_ticket.latest_decision = "normal"
                    db_ticket.consecutive_normal_days = (
                        db_ticket.consecutive_normal_days or 0
                    ) + 1

                # Auto-silence resume (§7.1.4 Step 4 "自动静默恢复"):
                if (
                    is_still_actionable
                    and prev_latest_decision in ("normal", "unknown")
                    and db_ticket.governance_status == "open"
                    and db_ticket.response is None
                    and db_ticket.remind_at is None
                ):
                    db_ticket.remind_at = now
                    # Audit is written after session commits

            # Audit auto-silence resume if applicable
            if (
                is_still_actionable
                and prev_latest_decision in ("normal", "unknown")
                and ticket.get("governance_status") == "open"
                and ticket.get("response") is None
                and ticket.get("remind_at") is None
            ):
                self._audit_repo.add_audit(
                    run_id, bot_id, owner_id,
                    action_taken=AuditAction.AUTO_SILENCE_RESUMED,
                    dry_run=0,
                )

            self._audit_repo.add_audit(
                run_id, bot_id, owner_id,
                check_result="actionable",
                governance_decision=record.get("governance_decision"),
                hit_dimensions=record.get("hit_dimensions"),
                action_taken=AuditAction.STILL_ACTIONABLE,
                dry_run=0,
            )

        return RecordProcessResult(
            worker_key=worker_key,
            entered_governance_scope=True,
            action="still_actionable",
            reason="active_ticket_exists_snapshot_refreshed",
            ticket_id=ticket.get("ticket_id"),
        )

    # ------------------------------------------------------------------
    # Internal: New ticket creation (§7.1.4 Step 6)
    # ------------------------------------------------------------------

    def _create_new_ticket(
        self,
        *,
        record: dict,
        worker_key: str,
        owner_id: str,
        bot_id: str,
        dt_version: str,
        run_id: str,
        dry_run: bool,
        notify_source: str,
        latest_closed: dict | None,
    ) -> RecordProcessResult:
        """Create new ticket + first_send notify (§7.1.4 Step 6)."""
        now = datetime.now()
        ticket_id = uuid.uuid4().hex
        notification_id = uuid.uuid4().hex
        owner_id_val = record.get("owner_id") or owner_id

        # Determine notification_md: check for review_rejected reopen template
        use_reopen_template = False
        reopen_ref_time: datetime | None = None
        if (
            latest_closed is not None
            and latest_closed.get("close_reason") == "review_rejected"
        ):
            use_reopen_template = True
            # Time priority: response_at > reviewed_at > closed_at
            reopen_ref_time = (
                latest_closed.get("response_at")
                or latest_closed.get("reviewed_at")
                or latest_closed.get("closed_at")
            )

        # Render notification markdown
        notification_md = self._render_notification_md(
            record=record,
            notification_id=notification_id,
            bot_id=bot_id,
            owner_id=owner_id_val,
            dt_version=dt_version,
            use_reopen_template=use_reopen_template,
            reopen_ref_time=reopen_ref_time,
        )

        if dry_run:
            # Return preview without writing
            self._audit_repo.add_audit(
                run_id, bot_id, owner_id_val,
                check_result="actionable",
                governance_decision=record.get("governance_decision"),
                hit_dimensions=record.get("hit_dimensions"),
                action_taken=AuditAction.ENQUEUED,
                dry_run=1,
            )
            return RecordProcessResult(
                worker_key=worker_key,
                entered_governance_scope=True,
                action="would_create",
                reason="no_active_ticket_and_no_cooldown",
                ticket_id=ticket_id,
                notification_md_preview=notification_md,
            )

        # CREATE task_record (self-managed session + flush)
        ticket_row = GovernanceTaskRecordDaily(
            ticket_id=ticket_id,
            worker_id=worker_key,
            active_worker=worker_key,
            bot_id=bot_id,
            owner_id=owner_id_val,
            bot_name=record.get("bot_name"),
            dt_version=dt_version,
            governance_decision="actionable",  # initial_decision (§5.6)
            latest_decision="actionable",
            hit_dimensions=record.get("hit_dimensions"),
            hit_dimensions_count=record.get("hit_dimensions_count"),
            governance_max_priority=record.get("governance_max_priority"),
            expected_token_saving=record.get("expected_token_saving"),
            saving_ratio=record.get("saving_ratio"),
            task_summary=record.get("task_summary"),
            notification_structured=record.get("notification_structured"),
            analysis_status=record.get("analysis_status"),
            governance_status="open",
            consecutive_normal_days=0,
            remind_at=None,  # No remind until first_send sent (§7.3.3)
            remind_count=0,
            last_seen_at=now,
            last_sync_at=now,
            last_decision_dt_version=dt_version,
        )
        self._task_repo.add_ticket(ticket_row)

        # CREATE notify_log — frozen snapshot at creation time (§5.6)
        notify_row = GovernanceNotifyLog(
            notification_id=notification_id,
            ticket_id=ticket_id,
            bot_id=bot_id,
            bot_name=record.get("bot_name"),
            owner_id=owner_id_val,
            worker_id=worker_key,
            dt_version=dt_version,
            governance_decision="actionable",  # freeze latest_decision at creation (§5.6)
            governance_cycle_id=ticket_id,  # NOT NULL — use ticket_id as cycle ID
            hit_dimensions=record.get("hit_dimensions"),
            hit_dimensions_count=record.get("hit_dimensions_count"),
            expected_token_saving=record.get("expected_token_saving"),
            saving_ratio=record.get("saving_ratio"),
            notification_md=notification_md,
            notification_structured=record.get("notification_structured"),
            governance_max_priority=record.get("governance_max_priority"),
            notify_status="pending",
            notify_type="first_send",
            notify_source=notify_source,
            notify_channel=getattr(self._config, "notify_channel", "markdown"),
            send_attempt_count=0,
        )
        # env auto-filled by ORM default=get_current_env (not in constructor)
        self._notify_repo.add_notification(notify_row)

        # Audit enqueued (self-managed session)
        self._audit_repo.add_audit(
            run_id, bot_id, owner_id_val,
            notification_id=notification_id,
            check_result="actionable",
            governance_decision=record.get("governance_decision"),
            hit_dimensions=record.get("hit_dimensions"),
            expected_token_saving=record.get("expected_token_saving"),
            saving_ratio=record.get("saving_ratio"),
            action_taken=AuditAction.ENQUEUED,
            dry_run=0,
        )

        return RecordProcessResult(
            worker_key=worker_key,
            entered_governance_scope=True,
            action="enqueued",
            reason="new_ticket_and_first_send_created",
            ticket_id=ticket_id,
            notification_md_preview=notification_md,
        )

    # ------------------------------------------------------------------
    # Internal: Batch quality validation (§7.2.4)
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_batch_quality(
        *,
        records: list[dict],
        total_count: int,
    ) -> list[str]:
        """Validate batch metadata — record count consistency (§7.2.4).

        Returns list of failure reasons; empty list means validation passed.
        """
        reasons: list[str] = []

        if len(records) != total_count:
            reasons.append(
                f"count_mismatch: expected={total_count}, actual={len(records)}"
            )

        return reasons

    # ------------------------------------------------------------------
    # Internal: Worker key validation (§5.4)
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_worker_key(worker_key: str) -> str | None:
        """Validate worker_key format (§5.4): single colon, both sides non-empty.

        Returns error message if invalid, None if valid.
        """
        if not worker_key:
            return "worker_key is empty"
        parts = worker_key.split(":", 1)
        if len(parts) != 2:
            return f"worker_key missing colon separator: {worker_key!r}"
        if not parts[0] or not parts[1]:
            return f"worker_key has empty side: {worker_key!r}"
        return None

    # ------------------------------------------------------------------
    # Internal: Notification rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _render_notification_md(
        *,
        record: dict,
        notification_id: str,
        bot_id: str,
        owner_id: str,
        dt_version: str,
        use_reopen_template: bool = False,
        reopen_ref_time: datetime | None = None,
    ) -> str:
        """Render notification markdown, with optional reopen template."""
        if use_reopen_template:
            # "重新治理" template (§7.1.4 Step 6)
            time_str = (
                reopen_ref_time.strftime("%Y-%m-%d %H:%M")
                if reopen_ref_time
                else "之前"
            )
            return (
                f"#### 🔄 重新治理通知 — {record.get('bot_name', '未知Bot')}\n\n"
                f"该治理项曾在 {time_str} 处理过反馈。"
                f"基于最新数据复核，当前仍需要继续跟进。\n\n"
                f"请参考以下建议处理；如有补充说明，也可以继续反馈。\n\n"
                f"**命中维度**: {record.get('hit_dimensions', '—')}\n"
                f"**数据日期**: {dt_version}\n"
            )

        # Standard first notification template
        return render_governance_notify(
            bot_name=record.get("bot_name", ""),
            dt_version=dt_version,
            hit_dimensions=record.get("hit_dimensions"),
            governance_max_priority=record.get("governance_max_priority"),
            expected_token_saving=record.get("expected_token_saving"),
            saving_ratio=record.get("saving_ratio"),
            task_summary=record.get("task_summary"),
            bot_id=bot_id,
            notification_id=notification_id,
            notification_structured=record.get("notification_structured"),
        )

    # ------------------------------------------------------------------
    # Internal: Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate_by_worker(records: list[dict]) -> list[dict]:
        """Remove duplicate worker_key entries, keeping last occurrence.

        When multiple records share the same worker_key, the last one
        wins (latest data from CSV).  This prevents UNIQUE constraint
        failures when the same worker appears more than once in a batch.

        Args:
            records: Raw records from offline-batch upload.

        Returns:
            Deduped record list.
        """
        seen: dict[str, int] = {}
        for idx, rec in enumerate(records):
            worker_key = rec.get("worker_id", "")
            if not worker_key or ":" not in worker_key:
                worker_key = f"{rec.get('owner_id', '')}:{rec.get('bot_id', '')}"
            seen[worker_key] = idx

        deduped = [records[idx] for idx in seen.values()]
        dup_count = len(records) - len(seen)

        if dup_count > 0:
            log.info(
                "[OfflineBatch] Deduplicated: %d → %d records "
                "(%d duplicate worker(s) removed)",
                len(records), len(deduped), dup_count,
            )

        return deduped