"""Governance cron service — time-driven tick (§7.3).

Replaces the old monolithic daily scan with a stateless, idempotent cron
tick that processes only time-driven actions:

  1. Sending timeout recovery (§7.3.1 Step 7)
  2. Send pending notifies (§7.3.1)
  3. Create reminder notifies (§7.3.2)
  4. Cancel non-sendable pending notifies (§7.3.5)
  5. Schedule due → waiting_review (§7.3.4)

No offline data judgment — that responsibility belongs to
``record_process_service`` (§7.1) and ``offline_batch`` (§7.2).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.core.economy.governance.contracts.enums import AuditAction
from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceNotifyLog,
    GovernanceTaskRecordDaily,
)
from agentclaw.community.core.economy.governance.services.notify_builder_service import (
    build_card_notification_data,
    build_governance_reason,
    build_tc_card_detail_link,
    render_governance_remind,
)


# ---------------------------------------------------------------------------
# Implementation constants (not in EconomyGovernanceConfig — these are
# deterministic knobs that never change per-environment; ops toggles
# stay in config, retry/timeout/cadence details stay here).
# ---------------------------------------------------------------------------

_MAX_SEND_ATTEMPTS: int = 5
"""Terminal failure threshold — after this many failed sends the notify is
marked as permanently failed and no further attempts are made."""

_SENDING_TIMEOUT_MINUTES: int = 30
"""Minutes before a stuck ``sending`` status reverts to ``pending``
so the next cron tick can reclaim it."""

_DEFAULT_REMIND_DELAYS_DAYS: tuple[int, ...] = (3, 7, 14)
"""Default reminder rhythm (days after the previous send).
Indexes correspond to remind_count: [0]=first reminder, [1]=second, etc."""

_DEFAULT_REPEAT_LAST_REMIND_DELAY: bool = True
"""When True, the last delay in _REMIND_DELAYS_DAYS repeats indefinitely;
when False, no further reminders are scheduled after the rhythm is exhausted."""


if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.contracts.protocols import (
        GovernanceNotifySender,
    )
    from agentclaw.community.core.economy.governance.repositories.audit_repo import (
        GovernanceAuditRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
        NotifyLogRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
        TaskRecordRepository,
    )
    from agentclaw.community.core.economy.governance.services.admin_service import (
        GovernanceAdminService,
    )
    from agentclaw.community.plugin_api.database_protocol import DatabasePlugin

log = logging.getLogger(__name__)


@dataclass
class CronTickSummary:
    """Structured result of a single cron tick."""

    run_id: str = ""
    duration_seconds: float = 0.0
    sent_count: int = 0
    failed_count: int = 0
    cancelled_count: int = 0
    reminders_created: int = 0
    schedule_due_count: int = 0
    timeout_recovered: int = 0
    auto_silence_closed: int = 0
    errors: int = 0
    dry_run: bool = False


class GovernanceBotService:
    """Time-driven cron orchestrator (§7.3).

    Stateless and idempotent — each tick processes whatever is currently
    pending / due, regardless of when the last tick ran.
    """

    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        task_repo: TaskRecordRepository,
        admin_svc: GovernanceAdminService,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        config: Any,  # EconomyGovernanceConfig
        notify_sender: GovernanceNotifySender,
        dingtalk_config: Any = None,  # GovernanceDingTalkConfig
    ) -> None:
        self._db = db
        self._task_repo = task_repo
        self._admin_svc = admin_svc
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._config = config
        self._notify_sender = notify_sender
        self._dingtalk_config = dingtalk_config

        # Parse remind_delays_days from config string (e.g. "3,7,14") or use default
        raw_delays = getattr(config, "remind_delays_days", None)
        if raw_delays:
            self._remind_delays_days = tuple(
                int(d.strip()) for d in str(raw_delays).split(",") if d.strip()
            )
        else:
            self._remind_delays_days = _DEFAULT_REMIND_DELAYS_DAYS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_cron_tick(
        self,
        dry_run: bool | None = None,
        run_id: str | None = None,
    ) -> CronTickSummary:
        """Execute a single cron tick (§7.3).

        Processes time-driven actions only:
          1. Sending timeout recovery
          2. Send pending notifies
          3. Create reminder notifies
          4. Cancel non-sendable pending
          5. Schedule due → waiting_review
          6. Auto-silence convergence (§7.2.6)
        """
        started_at = datetime.now()
        if dry_run is None:
            dry_run = self._config.dry_run
        if run_id is None:
            run_id = uuid.uuid4().hex

        summary = CronTickSummary(run_id=run_id, dry_run=dry_run)

        # Emergency brake check
        if self._admin_svc.is_paused():
            log.info("[GovernanceCron] Emergency brake active — skipping tick, run_id=%s", run_id)
            return summary

        # Step 1: Sending timeout recovery (§7.3.1 Step 7)
        if not dry_run:
            timeout_minutes = _SENDING_TIMEOUT_MINUTES
            recovered = self._notify_repo.recover_sending_timeout(
                timeout_minutes,
            )
            summary.timeout_recovered = recovered
            if recovered:
                log.info(
                    "[GovernanceCron] Recovered %d sending timeout(s), run_id=%s",
                    recovered, run_id,
                )

        # Step 2: Send pending notifies (§7.3.1)
        self._send_pending_notifies(
            run_id=run_id,
            dry_run=dry_run,
            summary=summary,
        )

        # Step 3: Create reminder notifies (§7.3.2)
        if not dry_run:
            self._create_reminder_notifies(
                run_id=run_id,
                summary=summary,
            )

        # Step 4: Cancel non-sendable pending (§7.3.5)
        # (handled inline during _send_pending_notifies)

        # Step 5: Schedule due → waiting_review (§7.3.4)
        if not dry_run:
            self._process_schedule_due(
                run_id=run_id,
                summary=summary,
            )

        # Step 6: Auto-silence convergence — close recovered tickets (§7.2.6)
        if not dry_run:
            self._process_auto_silence_converge(
                run_id=run_id,
                summary=summary,
            )

        summary.duration_seconds = (datetime.now() - started_at).total_seconds()
        all_zero = (
            summary.sent_count
            + summary.failed_count
            + summary.cancelled_count
            + summary.reminders_created
            + summary.schedule_due_count
            + summary.timeout_recovered
            + summary.auto_silence_closed
        ) == 0
        no_action_note = (
            " — no action taken (see above for reason: empty DB / weekend / dry_run)"
            if all_zero else ""
        )
        log.info(
            "[GovernanceCron] Tick completed: run_id=%s, sent=%d, failed=%d, "
            "cancelled=%d, reminders=%d, schedule_due=%d, timeout_recovered=%d, "
            "auto_silence_closed=%d, dry_run=%s, duration=%.1fs%s",
            run_id,
            summary.sent_count,
            summary.failed_count,
            summary.cancelled_count,
            summary.reminders_created,
            summary.schedule_due_count,
            summary.timeout_recovered,
            summary.auto_silence_closed,
            summary.dry_run,
            summary.duration_seconds,
            no_action_note,
        )
        return summary

    # ------------------------------------------------------------------
    # Backward compat: process_run delegates to process_cron_tick
    # ------------------------------------------------------------------

    async def process_run(
        self,
        scan_date: object = None,
        dry_run: bool | None = None,
        run_id: str | None = None,
        skip_delivery: bool = False,
        notify_source: str = "cron",
    ) -> CronTickSummary:
        """Backward-compat entry point — delegates to process_cron_tick."""
        return await self.process_cron_tick(dry_run=dry_run, run_id=run_id)

    # ------------------------------------------------------------------
    # Step 2: Send pending notifies (§7.3.1)
    # ------------------------------------------------------------------

    def _send_pending_notifies(
        self,
        run_id: str,
        dry_run: bool,
        summary: CronTickSummary,
    ) -> None:
        """Send pending notifies, checking task_record eligibility first."""
        now = datetime.now()
        pending = self._notify_repo.list_pending_for_cron()
        is_weekend = self._should_skip_delivery()
        if not pending:
            log.info(
                "[GovernanceCron] No pending notify found in DB — nothing to process, run_id=%s",
                run_id,
            )
        else:
            weekend_note = (
                " (all will be skipped: skip_weekends=true)" if is_weekend else ""
            )
            dry_run_note = " (no actual send, audit only)" if dry_run else ""
            log.info(
                "[GovernanceCron] Found %d pending notify(s)%s%s, run_id=%s",
                len(pending), weekend_note, dry_run_note, run_id,
            )

        skip_weekend_count = 0
        skip_dry_run_count = 0

        for notify_row in pending:
            try:
                # Check emergency brake
                if self._admin_svc.is_paused():
                    log.info("[GovernanceCron] Emergency brake — stopping send")
                    break

                # Check task_record eligibility (§7.3.1 Step 2)
                ticket = self._find_ticket_for_notify(notify_row)
                if ticket is None:
                    # No ticket found — cancel via self-managed session
                    self._notify_repo.cancel_pending_by_ticket(
                        notify_row.get("notification_id"),
                    )
                    summary.cancelled_count += 1
                    continue

                if ticket.get("governance_status") != "open":
                    self._audit_repo.add_audit(
                        run_id,
                        bot_id=notify_row.get("bot_id"),
                        owner_id=notify_row.get("owner_id"),
                        notification_id=notify_row.get("notification_id"),
                        check_result=notify_row.get("governance_decision"),
                        governance_decision=notify_row.get("governance_decision"),
                        action_taken=AuditAction.NOTIFY_CANCELLED_NON_OPEN,
                        source="online_cron",
                        dry_run=0,
                    )
                    self._notify_repo.cancel_pending_by_ticket(
                        notify_row.get("ticket_id"),
                    )
                    summary.cancelled_count += 1
                    continue

                if ticket.get("latest_decision") != "actionable":
                    self._audit_repo.add_audit(
                        run_id,
                        bot_id=notify_row.get("bot_id"),
                        owner_id=notify_row.get("owner_id"),
                        notification_id=notify_row.get("notification_id"),
                        check_result=notify_row.get("governance_decision"),
                        governance_decision=notify_row.get("governance_decision"),
                        action_taken=AuditAction.NOTIFY_CANCELLED_NOT_ACTIONABLE,
                        source="online_cron",
                        dry_run=0,
                    )
                    self._notify_repo.cancel_pending_by_ticket(
                        notify_row.get("ticket_id"),
                    )
                    summary.cancelled_count += 1
                    continue

                # Check send window (weekend skip)
                if self._should_skip_delivery():
                    # Weekend → keep pending, don't increment attempt count
                    skip_weekend_count += 1
                    continue

                if dry_run:
                    skip_dry_run_count += 1
                    continue

                # Atomic claim (§7.3.1 Step 4)
                claimed = self._notify_repo.claim_pending(
                    notify_row.get("notification_id"), now,
                )
                if not claimed:
                    continue  # Another consumer already claimed

                # Send (§7.3.1 Step 5)
                success = self._send_notification(notify_row)

                if success:
                    # Mark sent (§7.3.1 Step 5)
                    self._notify_repo.mark_sent(
                        notify_row.get("notification_id"),
                        external_message_id=notify_row.get("external_message_id"),
                        sent_at=now,
                    )
                    summary.sent_count += 1

                    # Audit: notification_sent (first_send or reminder)
                    audit_action = (
                        AuditAction.NOTIFICATION_SENT
                        if notify_row.get("notify_type") == "first_send"
                        else AuditAction.REMIND_SENT
                    )
                    self._audit_repo.add_audit(
                        run_id,
                        bot_id=notify_row.get("bot_id"),
                        owner_id=notify_row.get("owner_id"),
                        notification_id=notify_row.get("notification_id"),
                        check_result=notify_row.get("governance_decision"),
                        governance_decision=notify_row.get("governance_decision"),
                        hit_dimensions=notify_row.get("hit_dimensions"),
                        expected_token_saving=notify_row.get("expected_token_saving"),
                        saving_ratio=float(notify_row.get("saving_ratio"))
                        if notify_row.get("saving_ratio") else None,
                        action_taken=audit_action,
                        source="online_cron",
                        dry_run=0,
                    )

                    # Advance reminder chain (§7.3.3)
                    self._advance_reminder_chain(
                        ticket=ticket,
                        notify_row=notify_row,
                        now=now,
                        run_id=run_id,
                    )
                else:
                    # Failed → revert or terminal failure
                    max_attempts = _MAX_SEND_ATTEMPTS
                    # Read actual attempt count from the row after claim
                    attempt_count = notify_row.get("send_attempt_count") or 1
                    is_terminal = attempt_count >= max_attempts
                    self._notify_repo.mark_send_failed(
                        notify_row.get("notification_id"),
                        error_msg="Send failed",
                        is_terminal=is_terminal,
                    )
                    summary.failed_count += 1

                    # Audit: non-terminal or terminal send failure
                    audit_action = (
                        AuditAction.NOTIFY_FAILED_TERMINAL
                        if is_terminal
                        else AuditAction.NOTIFICATION_SEND_FAILED
                    )
                    self._audit_repo.add_audit(
                        run_id,
                        bot_id=notify_row.get("bot_id"),
                        owner_id=notify_row.get("owner_id"),
                        notification_id=notify_row.get("notification_id"),
                        check_result=notify_row.get("governance_decision"),
                        governance_decision=notify_row.get("governance_decision"),
                        hit_dimensions=notify_row.get("hit_dimensions"),
                        expected_token_saving=notify_row.get("expected_token_saving"),
                        saving_ratio=float(notify_row.get("saving_ratio"))
                        if notify_row.get("saving_ratio") else None,
                        action_taken=audit_action,
                        source="online_cron",
                        error_msg="Send failed" if not is_terminal
                        else f"Terminal failure after {attempt_count} attempts",
                        dry_run=0,
                    )

            except Exception:
                log.exception(
                    "[GovernanceCron] Error processing notify_id=%s",
                    notify_row.get("notification_id"),
                )
                summary.errors += 1

        if skip_weekend_count or skip_dry_run_count:
            reasons = []
            if skip_weekend_count:
                reasons.append(
                    f"weekend_skipped={skip_weekend_count} (skip_weekends=true, will retry on next weekday)"
                )
            if skip_dry_run_count:
                reasons.append(
                    f"dry_run_skipped={skip_dry_run_count} (dry_run=true, no actual send)"
                )
            log.info(
                "[GovernanceCron] Eligible pending notify skipped: %s, run_id=%s",
                "; ".join(reasons), run_id,
            )

    # ------------------------------------------------------------------
    # Step 3: Create reminder notifies (§7.3.2)
    # ------------------------------------------------------------------

    def _create_reminder_notifies(
        self,
        run_id: str,
        summary: CronTickSummary,
    ) -> None:
        """Create reminder notify for eligible tickets (§7.3.2).

        Conditions: open + latest_decision=actionable + remind_at <= now +
        response IS NULL + no existing pending/sending reminder.
        """
        now = datetime.now()

        # Find remindable tickets
        remindable = self._task_repo.list_remindable_tickets(now)

        for ticket in remindable:
            try:
                if self._admin_svc.is_paused():
                    break

                # Dedup check (§7.3.2)
                if ticket.get("ticket_id") and self._notify_repo.has_pending_or_sending_reminder(
                    ticket.get("ticket_id"),
                ):
                    continue

                # Create reminder notify_log
                notification_id = uuid.uuid4().hex
                notification_md = self._render_reminder_md(ticket, now)

                notify_row = GovernanceNotifyLog(
                    notification_id=notification_id,
                    ticket_id=ticket.get("ticket_id"),
                    bot_id=ticket.get("bot_id"),
                    bot_name=ticket.get("bot_name"),
                    owner_id=ticket.get("owner_id"),
                    worker_id=ticket.get("worker_id"),
                    dt_version=ticket.get("dt_version"),
                    governance_decision=ticket.get("latest_decision") or "actionable",
                    hit_dimensions=ticket.get("hit_dimensions"),
                    hit_dimensions_count=ticket.get("hit_dimensions_count"),
                    expected_token_saving=ticket.get("expected_token_saving"),
                    saving_ratio=ticket.get("saving_ratio"),
                    notification_md=notification_md,
                    notification_structured=ticket.get("notification_structured"),
                    governance_max_priority=ticket.get("governance_max_priority"),
                    notify_status="pending",
                    notify_type="reminder",
                    notify_source="online_cron",
                    notify_channel=self._config.notify_channel,
                    send_attempt_count=0,
                    # Sealed column — required by NOT NULL constraint
                    governance_cycle_id=ticket.get("ticket_id") or uuid.uuid4().hex,
                )
                self._notify_repo.add_notification(notify_row)

                # Clear remind_at (§7.3.2: ticket.remind_at = NULL)
                # NOTE: ticket is a read-only snapshot from self-managed session.
                # The remind_at clearing is done in the record_process_service
                # or admin_service layer when they manage ticket lifecycle.

                # Audit reminder scheduled
                self._audit_repo.add_audit(
                    run_id,
                    bot_id=ticket.get("bot_id"),
                    owner_id=ticket.get("owner_id"),
                    notification_id=notification_id,
                    check_result=ticket.get("latest_decision"),
                    governance_decision=ticket.get("latest_decision"),
                    hit_dimensions=ticket.get("hit_dimensions"),
                    expected_token_saving=ticket.get("expected_token_saving"),
                    saving_ratio=float(ticket.get("saving_ratio"))
                    if ticket.get("saving_ratio") else None,
                    action_taken=AuditAction.REMIND_SCHEDULED,
                    source="online_cron",
                    dry_run=0,
                )

                summary.reminders_created += 1

            except Exception:
                log.exception(
                    "[GovernanceCron] Error creating reminder for ticket_id=%s",
                    ticket.get("ticket_id"),
                )
                summary.errors += 1

    # ------------------------------------------------------------------
    # Step 5: Schedule due → waiting_review (§7.3.4)
    # ------------------------------------------------------------------

    def _process_schedule_due(
        self,
        run_id: str,
        summary: CronTickSummary,
    ) -> None:
        """Transition scheduled tickets with mute_until <= now to waiting_review."""
        now = datetime.now()
        due_tickets = self._task_repo.list_scheduled_due(now)

        for ticket in due_tickets:
            try:
                # Update ticket via self-managed session
                with self._db.orm_session() as s:
                    db_ticket = (
                        s.query(GovernanceTaskRecordDaily)
                        .filter(
                            GovernanceTaskRecordDaily.ticket_id == ticket.get("ticket_id"),
                        )
                        .one_or_none()
                    )
                    if db_ticket is None:
                        continue
                    db_ticket.governance_status = "waiting_review"
                    db_ticket.review_reason = "schedule_due"
                    db_ticket.remind_at = None

                # Cancel pending notify (waiting_review = no sends)
                if ticket.get("ticket_id"):
                    self._notify_repo.cancel_pending_by_ticket(
                        ticket.get("ticket_id"),
                    )

                self._audit_repo.add_audit(
                    run_id,
                    bot_id=ticket.get("bot_id"),
                    owner_id=ticket.get("owner_id"),
                    check_result=ticket.get("latest_decision"),
                    governance_decision=ticket.get("governance_decision"),
                    hit_dimensions=ticket.get("hit_dimensions"),
                    expected_token_saving=ticket.get("expected_token_saving"),
                    saving_ratio=float(ticket.get("saving_ratio"))
                    if ticket.get("saving_ratio") else None,
                    action_taken=AuditAction.SCHEDULE_DUE,
                    source="online_cron",
                    dry_run=0,
                )

                summary.schedule_due_count += 1

            except Exception:
                log.exception(
                    "[GovernanceCron] Error processing schedule_due for ticket_id=%s",
                    ticket.get("ticket_id"),
                )
                summary.errors += 1

    # ------------------------------------------------------------------
    # Step 6: Auto-silence convergence — close recovered tickets (§7.2.6)
    # ------------------------------------------------------------------

    def _process_auto_silence_converge(
        self,
        run_id: str,
        summary: CronTickSummary,
    ) -> None:
        """Close open tickets whose ``consecutive_normal_days`` reached the
        auto-silence threshold (§7.2.6).

        When a ticket has been ``normal`` for ``auto_silence_close_days``
        consecutive data-refresh cycles, it is considered recovered and
        closed automatically with ``close_reason=auto_silenced_normal``.
        """
        threshold = getattr(self._config, "auto_silence_close_days", 7)
        if threshold <= 0:
            return  # Feature disabled

        eligible = self._task_repo.list_auto_silence_eligible(
            min_consecutive_days=threshold,
        )

        now = datetime.now()
        for ticket in eligible:
            try:
                # Update ticket via self-managed session
                with self._db.orm_session() as s:
                    db_ticket = (
                        s.query(GovernanceTaskRecordDaily)
                        .filter(
                            GovernanceTaskRecordDaily.ticket_id == ticket.get("ticket_id"),
                        )
                        .one_or_none()
                    )
                    if db_ticket is None:
                        continue
                    db_ticket.governance_status = "closed"
                    db_ticket.close_reason = "auto_silenced_normal"
                    db_ticket.closed_at = now
                    db_ticket.active_worker = None
                    db_ticket.remind_at = None

                # Cancel any pending notify for this ticket
                if ticket.get("ticket_id"):
                    self._notify_repo.cancel_pending_by_ticket(
                        ticket.get("ticket_id"),
                    )

                self._audit_repo.add_audit(
                    run_id,
                    bot_id=ticket.get("bot_id"),
                    owner_id=ticket.get("owner_id"),
                    check_result=ticket.get("latest_decision"),
                    governance_decision=ticket.get("governance_decision"),
                    hit_dimensions=ticket.get("hit_dimensions"),
                    expected_token_saving=ticket.get("expected_token_saving"),
                    saving_ratio=float(ticket.get("saving_ratio"))
                    if ticket.get("saving_ratio") else None,
                    action_taken=AuditAction.AUTO_SILENCE_CONVERGED,
                    source="online_cron",
                    dry_run=0,
                )

                summary.auto_silence_closed += 1

            except Exception:
                log.exception(
                    "[GovernanceCron] Error in auto_silence_converge "
                    "for ticket_id=%s",
                    ticket.get("ticket_id"),
                )
                summary.errors += 1

        if eligible:
            log.info(
                "[GovernanceCron] Auto-silence convergence: threshold=%d, "
                "closed=%d, run_id=%s",
                threshold, len(eligible), run_id,
            )

    # ------------------------------------------------------------------
    # Reminder chain advancement (§7.3.3)
    # ------------------------------------------------------------------

    def _advance_reminder_chain(
        self,
        *,
        ticket: dict,
        notify_row: dict,
        now: datetime,
        run_id: str,
    ) -> None:
        """Advance reminder chain after a successful send (§7.3.3).

        Sets remind_at based on remind_delays_days and current remind_count:
        - first_send sent → remind_at = now + delays[0]
        - reminder N sent → remind_at = now + delays[N] (or last delay if repeat)

        Since repos now use self-managed sessions, ticket updates must go
        through a dedicated DB session.
        """
        remind_delays = self._remind_delays_days
        repeat_last = _DEFAULT_REPEAT_LAST_REMIND_DELAY

        new_remind_at: datetime | None = None

        if notify_row.get("notify_type") == "first_send":
            # First send → schedule first reminder
            if remind_delays:
                new_remind_at = now + timedelta(days=remind_delays[0])
            # remind_count stays 0 until reminder actually sent

        elif notify_row.get("notify_type") == "reminder":
            # Reminder sent → increment remind_count and schedule next
            count = (ticket.get("remind_count") or 0) + 1

            if count < len(remind_delays):
                # Use the next delay in the rhythm
                new_remind_at = now + timedelta(days=remind_delays[count])
            elif repeat_last and remind_delays:
                # Repeat last delay indefinitely
                new_remind_at = now + timedelta(days=remind_delays[-1])
            else:
                # No more reminders
                new_remind_at = None

        # Persist remind_at change
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTaskRecordDaily)
                .filter(
                    GovernanceTaskRecordDaily.ticket_id == ticket.get("ticket_id"),
                )
                .one_or_none()
            )
            if db_ticket is not None:
                if notify_row.get("notify_type") == "reminder":
                    db_ticket.remind_count = (db_ticket.remind_count or 0) + 1
                db_ticket.remind_at = new_remind_at

    # ------------------------------------------------------------------
    # Send dispatch
    # ------------------------------------------------------------------

    def _send_notification(self, notify_row: dict) -> bool:
        """Send a notification via configured channel.

        Returns True on success, False on failure.
        """
        user_id = notify_row.get("owner_id")
        notify_channel = notify_row.get("notify_channel", "markdown") or "markdown"

        if notify_channel == "tc_card":
            external_id = self._try_send_tc_card(notify_row, user_id)
            if external_id is not None:
                notify_row["external_message_id"] = external_id
                return True
            # Degrade to Markdown
            notify_row["notify_channel"] = "markdown"

        # Markdown channel
        content = notify_row.get("notification_md") or ""
        external_id = self._notify_sender.send_markdown(
            user_id=user_id,
            title="🔔 Bot 治理通知" if notify_row.get("notify_type") == "first_send" else "⚠️ 治理通知提醒",
            content=content,
        )
        if external_id:
            notify_row["external_message_id"] = external_id
            return True
        return False

    def _try_send_tc_card(
        self, notify_row: dict, user_id: str,
    ) -> str | None:
        """Attempt TC card send, returns external_message_id or None."""
        try:
            reason = build_governance_reason(
                notification_structured=notify_row.get("notification_structured"),
                bot_name=notify_row.get("bot_name"),
                dt_version=notify_row.get("dt_version"),
                hit_dimensions=notify_row.get("hit_dimensions"),
                governance_max_priority=notify_row.get("governance_max_priority"),
                expected_token_saving=notify_row.get("expected_token_saving"),
                saving_ratio=notify_row.get("saving_ratio"),
                task_summary=None,
            )

            notification_data = build_card_notification_data(
                notification_structured=notify_row.get("notification_structured"),
                notification_id=notify_row.get("notification_id"),
                bot_id=notify_row.get("bot_id"),
                bot_name=notify_row.get("bot_name"),
                owner_id=notify_row.get("owner_id"),
                dt_version=notify_row.get("dt_version"),
                expected_token_saving=notify_row.get("expected_token_saving"),
                saving_ratio=notify_row.get("saving_ratio"),
                governance_max_priority=notify_row.get("governance_max_priority"),
            )
            iframe_callback_url = (
                self._dingtalk_config.iframe_callback_url
                if self._dingtalk_config else ""
            )
            detail_link = build_tc_card_detail_link(
                bot_id=notify_row.get("bot_id"),
                card_id=self._config.tc_card_id,
                notification_data=notification_data,
                base_url=self._config.tc_card_preview_url,
                iframe_callback_url=iframe_callback_url,
                staff_id=user_id,
            )

            return self._notify_sender.send_tc_card(
                user_id=user_id,
                reason=reason,
                detail_link=detail_link,
                bot_id=notify_row.get("bot_id"),
                card_id=self._config.tc_card_id,
                notification_data=notification_data,
                out_track_id_prefix="gov-notify",
            )
        except Exception:
            log.exception(
                "[GovernanceCron] TC card send failed for notification_id=%s",
                notify_row.get("notification_id"),
            )
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_ticket_for_notify(
        self, notify_row: dict,
    ) -> dict | None:
        """Find the ticket for a notify_log row via ticket_id."""
        if not notify_row.get("ticket_id"):
            return None
        return self._task_repo.find_by_ticket_id(notify_row.get("ticket_id"))

    @staticmethod
    def _render_reminder_md(
        ticket: dict, now: datetime,
    ) -> str:
        """Render reminder markdown from ticket fields."""
        days_since = (now - ticket.get("gmt_create")).days if ticket.get("gmt_create") else 0
        return render_governance_remind(
            bot_name=ticket.get("bot_name"),
            dt_version=ticket.get("dt_version"),
            hit_dimensions=ticket.get("hit_dimensions"),
            governance_max_priority=ticket.get("governance_max_priority"),
            remind_count=ticket.get("remind_count") or 0,
            days_since_create=days_since,
            bot_id=ticket.get("bot_id"),
            notification_id="",  # Will be filled when notify is created
        )

    def _should_skip_delivery(self) -> bool:
        """Return True when skip_weekends is enabled and today is Sat/Sun."""
        if not self._config.skip_weekends:
            return False
        weekday = datetime.now().weekday()
        return weekday >= 5

    # ------------------------------------------------------------------
    # Legacy data-readiness method (kept for backward compat)
    # ------------------------------------------------------------------

    def is_data_ready(self, scan_date: object) -> bool:
        """Legacy method — always returns True for cron mode."""
        return True

    def write_data_not_ready_audit_once(self, scan_date: object, *, reason: str) -> None:
        """Legacy method — no-op in cron mode."""
        pass