"""[编排] Record process and offline-batch service for governance task_record / notify_log.

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
Ticket mutations (refresh, whitelist-close) are performed via Repo
command methods.
"""
from __future__ import annotations

from agentclaw.community.log import get_logger
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    CloseReason,
    GovernanceStatus,
    NotifyType,
)
from agentclaw.community.core.economy.governance.domain.notification import FrozenSnapshot, GovernanceNotification
from agentclaw.community.core.economy.governance.domain.record import GovernanceRecord
from agentclaw.community.core.economy.governance.domain.ticket import (
    GovernanceTicket,
    MutableSnapshot,
)
from agentclaw.community.core.economy.governance.services.notify_render_service import (
    NotifyRenderService,
)
from agentclaw.community.core.economy.governance.services.service_protocols import (
    GovernanceLifecycleServiceProtocol,
)


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

log = get_logger(__name__)


# ── Result types ─────────────────────────────────────────────────────────


@dataclass
class RecordProcessResult:
    """Result of processing a single record through process_record."""

    worker_key: str
    entered_governance_scope: bool = False
    action: str = ""  # enqueued / would_create / still_actionable / whitelist_filtered / cooldown_filtered / whitelist_closed / invalid / error
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
        task_repo: TaskRecordRepository,
        whitelist_repo: GovernanceWhitelistRepository,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        config: Any,  # EconomyGovernanceConfig
        lifecycle_svc: GovernanceLifecycleServiceProtocol,
        render_svc: NotifyRenderService,
    ) -> None:
        self._task_repo = task_repo
        self._whitelist_repo = whitelist_repo
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._config = config
        self._lifecycle_svc = lifecycle_svc
        self._render_svc = render_svc

    # ------------------------------------------------------------------
    # Public: process_record (§7.1.4)
    # ------------------------------------------------------------------

    def process_record(
        self,
        record: GovernanceRecord,
        *,
        run_id: str,
        dry_run: bool = False,
        notify_source: str = "offline_batch",
    ) -> RecordProcessResult:
        """Process a single governance record (§7.1.4 Steps 1–6).

        Args:
            record: :class:`GovernanceRecord` 领域模型(owner_id/bot_id/
                governance_decision/dt_version 必填,数据字段可选)。
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
        worker_key = record.effective_worker_key
        # Extract owner_id / bot_id for downstream use:worker_id 优先拆,
        # 否则取 record 的身份字段(必填非空)。
        if record.worker_id and ":" in record.worker_id:
            owner_id, bot_id = record.worker_id.split(":", 1)
        else:
            owner_id = record.owner_id
            bot_id = record.bot_id

        validation_error = self._validate_worker_key(worker_key)
        if validation_error:
            log.warning(
                "[RecordProcess] Invalid worker_key=%s: %s",
                worker_key, validation_error,
            )
            return RecordProcessResult(
                worker_key=worker_key,
                action="invalid",
                reason=validation_error,
            )

        dt_version = record.dt_version

        # Step 2: Whitelist filter (§7.1.4 Step 2) — point query
        is_whitelisted = self._whitelist_repo.is_whitelisted(bot_id, owner_id)

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
            and latest_closed.cooldown_until is not None
            and latest_closed.cooldown_until > now
        ):
            # Cooldown active → skip
            log.info(
                "[RecordProcess] Cooldown filtered: worker_key=%s, "
                "cooldown_until=%s, ticket_id=%s",
                worker_key,
                latest_closed.cooldown_until.isoformat(),
                latest_closed.ticket_id,
            )
            if not dry_run:
                self._audit_repo.add_audit(
                    run_id, bot_id, owner_id,
                    check_result="actionable",
                    governance_decision=record.governance_decision,
                    hit_dimensions=record.hit_dimensions,
                    action_taken=AuditAction.COOLDOWN_FILTERED,
                    dry_run=0,
                )
            return RecordProcessResult(
                worker_key=worker_key,
                entered_governance_scope=False,
                action="cooldown_filtered",
                reason=f"cooldown_until={latest_closed.cooldown_until.isoformat()}",
                ticket_id=latest_closed.ticket_id,
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
        records: list[GovernanceRecord],
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
            records: List of :class:`GovernanceRecord` (governance records).
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
            except Exception as e:
                log.exception(
                    "[OfflineBatch] Error processing record worker_key=%s",
                    rec.effective_worker_key,
                )
                result.errors += 1
                # 失败记录回传:action="error" + worker_key + 截断 reason(200),
                # 供调用方定点重传(与 errors 计数并存)
                result.upsert_results.append(RecordProcessResult(
                    worker_key=rec.effective_worker_key,
                    action="error",
                    reason=f"{type(e).__name__}: {e}"[:200],
                ))

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
                "[OfflineBatch] dry_run=True — run_id=%s, records=%d",
                run_id, len(records),
            )
            return result

        # Action distribution summary
        action_counts: dict[str, int] = {}
        for pr in result.upsert_results:
            action_counts[pr.action] = action_counts.get(pr.action, 0) + 1

        log.info(
            "[OfflineBatch] Completed: batch_id=%s, run_id=%s, "
            "records=%d, errors=%d, quality_skipped=%s, actions=%s",
            batch_id, run_id, len(records),
            result.errors,
            result.batch_quality_skipped,
            action_counts,
        )
        return result

    # ------------------------------------------------------------------
    # Internal: Whitelist handling (§7.1.4 Step 2)
    # ------------------------------------------------------------------

    def _handle_whitelist_hit(
        self,
        *,
        active_ticket: GovernanceTicket | None,
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

        # Whitelist hit + active ticket → close ticket (§7.2.7) via driver
        # service (sole driver of the ticket machine). Driver orchestrates
        # the close + cancel-pending-notify side effect atomically.
        if not dry_run:
            self._lifecycle_svc.close_for_whitelist_hit(
                active_ticket.ticket_id, now=now,
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
            ticket_id=active_ticket.ticket_id,
        )

    # ------------------------------------------------------------------
    # Internal: Active ticket refresh (§7.1.4 Step 4)
    # ------------------------------------------------------------------

    def _handle_active_ticket_refresh(
        self,
        *,
        ticket: GovernanceTicket,
        record: GovernanceRecord,
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
        prev_latest_decision = ticket.current_decision

        # Guard: reject stale dt_version (older or equal → skip refresh)
        existing_dt = ticket.dt_version or ""
        if dt_version and existing_dt and dt_version <= existing_dt:
            log.debug(
                "[RecordProcess] Skip refresh: incoming dt_version=%s "
                "<= existing dt_version=%s, worker_key=%s",
                dt_version, existing_dt, worker_key,
            )
            self._audit_repo.add_audit(
                run_id, bot_id, owner_id,
                check_result="actionable",
                governance_decision=record.governance_decision,
                hit_dimensions=record.hit_dimensions,
                action_taken=AuditAction.STILL_ACTIONABLE,
                dry_run=0,
            )
            return RecordProcessResult(
                worker_key=worker_key,
                entered_governance_scope=True,
                action="still_actionable",
                reason="stale_dt_version_skipped",
                ticket_id=ticket.ticket_id,
            )

        if not dry_run:
            # Refresh snapshot fields via dedicated session
            # 等价原 record.get("governance_decision", "actionable"):
            # 字段缺失/None → "actionable";空串保持空串(不同于 or 短路)。
            incoming_decision = (
                record.governance_decision if record.governance_decision is not None
                else "actionable"
            )
            is_still_actionable = incoming_decision == "actionable"

            # Compute consecutive_normal_days and remind_at
            if is_still_actionable:
                consecutive_days = 0
            else:
                consecutive_days = (ticket.consecutive_normal_days or 0) + 1

            # Auto-silence resume: if actionable + prev was normal + open + no feedback + no remind
            should_resume_remind = (
                is_still_actionable
                and prev_latest_decision in ("normal", "unknown")
                and ticket.governance_status == GovernanceStatus.OPEN
                and ticket.user_feedback is None
                and ticket.remind_at is None
            )
            # refresh_snapshot sentinel: "" = don't touch, datetime = set, None = clear
            effective_remind_at: datetime | None | object = now if should_resume_remind else ""

            # Snapshot refresh via driver service (sole driver). Driver
            # handles bot_name (identity) + remind_at sentinel passthrough
            # and persists via to_orm (snapshot path).
            self._lifecycle_svc.refresh_snapshot(
                ticket.ticket_id,
                dt_version=dt_version,
                bot_name=record.bot_name,
                triggered_dimensions=record.hit_dimensions,
                hit_dimensions_count=record.hit_dimensions_count,
                severity=record.governance_max_priority,
                estimated_saving_tokens=record.expected_token_saving,
                saving_ratio=record.saving_ratio,
                task_summary=record.task_summary,
                notification_structured=record.notification_structured,
                analysis_status=record.analysis_status,
                current_decision="actionable" if is_still_actionable else "normal",
                consecutive_normal_days=consecutive_days,
                last_seen_at=now,
                last_sync_at=now,
                last_decision_dt_version=dt_version,
                remind_at=effective_remind_at,
            )

            # Audit auto-silence resume if applicable
            if (
                is_still_actionable
                and prev_latest_decision in ("normal", "unknown")
                and ticket.governance_status == GovernanceStatus.OPEN
                and ticket.user_feedback is None
                and ticket.remind_at is None
            ):
                self._audit_repo.add_audit(
                    run_id, bot_id, owner_id,
                    action_taken=AuditAction.AUTO_SILENCE_RESUMED,
                    dry_run=0,
                )

            self._audit_repo.add_audit(
                run_id, bot_id, owner_id,
                check_result="actionable",
                governance_decision=record.governance_decision,
                hit_dimensions=record.hit_dimensions,
                action_taken=AuditAction.STILL_ACTIONABLE,
                dry_run=0,
            )

        return RecordProcessResult(
            worker_key=worker_key,
            entered_governance_scope=True,
            action="still_actionable",
            reason="active_ticket_exists_snapshot_refreshed",
            ticket_id=ticket.ticket_id,
        )

    # ------------------------------------------------------------------
    # Internal: New ticket creation (§7.1.4 Step 6)
    # ------------------------------------------------------------------

    def _create_new_ticket(
        self,
        *,
        record: GovernanceRecord,
        worker_key: str,
        owner_id: str,
        bot_id: str,
        dt_version: str,
        run_id: str,
        dry_run: bool,
        notify_source: str,
        latest_closed: GovernanceTicket | None,
    ) -> RecordProcessResult:
        """Create new ticket + first_send notify (§7.1.4 Step 6)."""
        now = datetime.now()
        ticket_id = uuid.uuid4().hex
        notification_id = uuid.uuid4().hex
        # owner_id_val:record.owner_id 优先(必填非空),空则回退 Step1 从 worker_id 拆出的 owner_id
        owner_id_val = record.owner_id or owner_id

        # Determine notification_md: check for review_rejected reopen template
        use_reopen_template = False
        reopen_ref_time: datetime | None = None
        if (
            latest_closed is not None
            and latest_closed.close_reason == CloseReason.REVIEW_REJECTED
        ):
            use_reopen_template = True
            # Time priority: response_at > reviewed_at > closed_at
            reopen_ref_time = (
                latest_closed.feedback_at
                or latest_closed.reviewed_at
                or latest_closed.closed_at
            )

        # Render notification markdown
        notification_md = self._render_svc.render_first_notification_md(
            record,
            dt_version=dt_version,
            use_reopen_template=use_reopen_template,
            reopen_ref_time=reopen_ref_time,
        )

        if dry_run:
            # Return preview without writing
            self._audit_repo.add_audit(
                run_id, bot_id, owner_id_val,
                check_result="actionable",
                governance_decision=record.governance_decision,
                hit_dimensions=record.hit_dimensions,
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

        # CREATE task_record — build domain model then delegate to the
        # driver service (sole driver of ticket creation). Same-method
        # consistency with the notify_log domain-model construction below
        # (L651 GovernanceNotification.create). The driver persists + audits.
        ticket_model = GovernanceTicket.create(
            ticket_id=ticket_id,
            worker_id=worker_key,
            bot_id=bot_id,
            owner_id=owner_id_val,
            bot_name=record.bot_name,
            snapshot=MutableSnapshot(
                dt_version=dt_version,
                initial_decision="actionable",  # initial_decision (§5.6)
                current_decision="actionable",
                triggered_dimensions=record.hit_dimensions,
                hit_dimensions_count=record.hit_dimensions_count,
                severity=record.governance_max_priority,
                estimated_saving_tokens=record.expected_token_saving,
                saving_ratio=record.saving_ratio,
                task_summary=record.task_summary,
                notification_structured=record.notification_structured,
                analysis_status=record.analysis_status,
                consecutive_normal_days=0,
                last_decision_dt_version=dt_version,
                last_seen_at=now,
                last_sync_at=now,
            ),
        )
        # Remind chained by scan after first_send; None until then (§7.3.3).
        assert ticket_model.remind_at is None
        self._lifecycle_svc.open_ticket(ticket=ticket_model)

        # CREATE notify_log — frozen snapshot at creation time (§5.6)
        notify_row = GovernanceNotification.create(
            notification_id=notification_id,
            ticket_id=ticket_id,
            bot_id=bot_id,
            bot_name=record.bot_name,
            owner_id=owner_id_val,
            worker_id=worker_key,
            snapshot=FrozenSnapshot(
                dt_version=dt_version,
                decision_at_create="actionable",  # freeze latest_decision (§5.6)
                triggered_dimensions=record.hit_dimensions,
                hit_dimensions_count=record.hit_dimensions_count,
                severity=record.governance_max_priority,
                estimated_saving_tokens=record.expected_token_saving,
                saving_ratio=record.saving_ratio,
                notification_md=notification_md,
                notification_structured=record.notification_structured,
            ),
            notify_type=NotifyType.FIRST_SEND,
            notify_source=notify_source,
            channel=getattr(self._config, "notify_channel", "markdown"),
        )
        # env auto-filled by ORM default=get_current_env (not in constructor)
        self._notify_repo.add_notification(notify_row)

        # Audit enqueued (self-managed session)
        self._audit_repo.add_audit(
            run_id, bot_id, owner_id_val,
            notification_id=notification_id,
            check_result="actionable",
            governance_decision=record.governance_decision,
            hit_dimensions=record.hit_dimensions,
            expected_token_saving=record.expected_token_saving,
            saving_ratio=record.saving_ratio,
            action_taken=AuditAction.ENQUEUED,
            dry_run=0,
        )

        log.info(
            "[RecordProcess] Enqueued: worker_key=%s, ticket_id=%s, "
            "bot_id=%s, owner_id=%s, dt_version=%s",
            worker_key, ticket_id, bot_id, owner_id_val, dt_version,
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
        records: list[GovernanceRecord],
        total_count: int,
    ) -> list[str]:
        """Validate batch metadata — record count consistency (§7.2.4).

        total_count <= 0 视为「生产者未提供预期值」,跳过 count 校验(不误报)。
        仅当显式提供正值且与实际不符时才判 mismatch。

        Returns list of failure reasons; empty list means validation passed.
        """
        reasons: list[str] = []

        if total_count > 0 and len(records) != total_count:
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
    # Internal: Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate_by_worker(records: list[GovernanceRecord]) -> list[GovernanceRecord]:
        """Remove duplicate worker_key entries, keeping last occurrence.

        When multiple records share the same worker_key, the last one
        wins (latest data from CSV).  This prevents UNIQUE constraint
        failures when the same worker appears more than once in a batch.

        Args:
            records: Raw records (GovernanceRecord) from offline-batch upload.

        Returns:
            Deduped record list。同 worker_key 冲突保留 dt_version 最大(最新)者,
            防乱序批次旧 dt 挤掉新 dt。
        """
        # key=worker_key, value=该 key 下 dt_version 最大的 record
        seen: dict[str, GovernanceRecord] = {}
        for rec in records:
            worker_key = rec.effective_worker_key
            existing = seen.get(worker_key)
            # dt_version 字符串字典序 = YYYYMMDD 时序;保留更大(更新)者
            if existing is None or rec.dt_version >= existing.dt_version:
                seen[worker_key] = rec

        deduped = list(seen.values())
        dup_count = len(records) - len(deduped)

        if dup_count > 0:
            log.info(
                "[OfflineBatch] Deduplicated: %d → %d records "
                "(%d duplicate worker(s) removed, kept latest dt_version)",
                len(records), len(deduped), dup_count,
            )

        return deduped