"""TaskRecord repository — ``ac_governance_task_record_daily`` ticket lifecycle.

Ticket CRUD (query), lifecycle mutations, admin delete/count, and test
seeding for the governance task_record_daily table.

Follows the ``DatabasePlugin`` self-managed session pattern
(see ``harness_patch_record_repository``): each method opens its
own ``orm_session()`` — self-managed sessions; env is resolved
internally via ``get_current_env()``.
"""
from __future__ import annotations

from datetime import datetime

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.economy.governance.repositories.orm import GovernanceTicketOrm
from agentclaw.community.core.economy.governance.domain.domain import GovernanceTicket
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


log = get_logger(__name__)


def _extract_owner_id(worker_id: str) -> str:
    """Extract owner_id from ``worker_id`` ('{owner_id}:{bot_id}').

    Follows the established convention: single split on first colon.
    """
    return worker_id.split(":", 1)[0]


class TaskRecordRepository:
    """Ticket lifecycle access to ``ac_governance_task_record_daily``."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Ticket CRUD (query / read)
    # ------------------------------------------------------------------

    def find_active_ticket(
        self, active_worker: str,
    ) -> GovernanceTicket | None:
        """Find the active ticket for an active_worker (owner_id:bot_id).

        Active = governance_status IN ('open', 'scheduled', 'waiting_review').

        Returns:
            :class:`GovernanceTicket` or ``None`` if no active ticket exists.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.active_worker == active_worker,
                    GovernanceTicketOrm.governance_status.in_(
                        ("open", "scheduled", "waiting_review"),
                    ),
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            return GovernanceTicket.from_orm(obj) if obj else None

    def find_by_ticket_id(
        self, ticket_id: str,
    ) -> GovernanceTicket | None:
        """Find a ticket by its stable UUID (ticket_id).

        Returns:
            :class:`GovernanceTicket` or ``None``.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            return GovernanceTicket.from_orm(obj) if obj else None

    def find_latest_closed_by_worker(
        self, worker_id: str,
    ) -> GovernanceTicket | None:
        """Find most recently closed ticket for a worker (cooldown & review_rejected check).

        Ordered by closed_at DESC.

        Returns:
            :class:`GovernanceTicket` or ``None`` if no closed ticket exists.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.worker_id == worker_id,
                    GovernanceTicketOrm.governance_status == "closed",
                    GovernanceTicketOrm.env == _env,
                )
                .order_by(
                    GovernanceTicketOrm.closed_at.desc(),
                    GovernanceTicketOrm.gmt_modified.desc(),
                )
                .first()
            )
            return GovernanceTicket.from_orm(obj) if obj else None

    def list_active_open_tickets(
        self,
    ) -> list[GovernanceTicket]:
        """List all open tickets with active_worker set (for auto_silence).

        Used by offline-batch to find active open tickets not in current batch.

        Returns:
            List of :class:`GovernanceTicket`.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status == "open",
                    GovernanceTicketOrm.active_worker.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def list_scheduled_due(
        self, now: datetime,
    ) -> list[GovernanceTicket]:
        """Find scheduled tickets where mute_until <= now (schedule_due).

        These tickets should transition from scheduled -> waiting_review.

        Returns:
            List of :class:`GovernanceTicket`.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status == "scheduled",
                    GovernanceTicketOrm.mute_until <= now,
                    GovernanceTicketOrm.mute_until.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def list_auto_silence_eligible(
        self,
        *,
        min_consecutive_days: int,
    ) -> list[GovernanceTicket]:
        """Find open tickets eligible for auto-silence convergence (7.2.6).

        Conditions: governance_status='open' + latest_decision='normal' +
        consecutive_normal_days >= min_consecutive_days + active_worker set.

        Args:
            min_consecutive_days: ``auto_silence_close_days`` from config.

        Returns:
            List of :class:`GovernanceTicket` meeting the convergence threshold.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status == "open",
                    GovernanceTicketOrm.latest_decision == "normal",
                    GovernanceTicketOrm.consecutive_normal_days
                    >= min_consecutive_days,
                    GovernanceTicketOrm.active_worker.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def list_remindable_tickets(
        self, now: datetime,
    ) -> list[GovernanceTicket]:
        """Find tickets eligible for reminder creation (7.3.2).

        Conditions: open + latest_decision=actionable + remind_at <= now
        + remind_at IS NOT NULL + response IS NULL + active_worker set.

        Returns:
            List of :class:`GovernanceTicket`.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status == "open",
                    GovernanceTicketOrm.latest_decision == "actionable",
                    GovernanceTicketOrm.remind_at <= now,
                    GovernanceTicketOrm.remind_at.isnot(None),
                    GovernanceTicketOrm.response.is_(None),
                    GovernanceTicketOrm.active_worker.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def list_tickets_by_owner_and_statuses(
        self,
        owner_id: str,
        statuses: list[str],
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[GovernanceTicket]:
        """Owner's tickets in the given statuses, newest first, paged.

        Returns:
            List of :class:`GovernanceTicket`.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.owner_id == owner_id,
                    GovernanceTicketOrm.governance_status.in_(statuses),
                    GovernanceTicketOrm.env == _env,
                )
                .order_by(GovernanceTicketOrm.gmt_create.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [GovernanceTicket.from_orm(r) for r in rows]

    def count_active_open(
        self,
    ) -> int:
        """Count all active open tickets (for admin dashboard)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            return (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status.in_(
                        ("open", "scheduled", "waiting_review"),
                    ),
                    GovernanceTicketOrm.active_worker.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .count()
            )

    def find_ticket_by_notification_id(
        self, notification_id: str,
    ) -> GovernanceTicket | None:
        """Find a ticket via its notify_log's notification_id.

        Used by feedback_service: notification_id -> notify_log.ticket_id -> task_record.

        Returns:
            :class:`GovernanceTicket` or ``None``.
        """
        from agentclaw.community.core.economy.governance.repositories.orm import (
            GovernanceNotificationOrm,
        )

        _env = get_current_env()
        with self._db.orm_session() as s:
            notify_row = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.notification_id == notification_id,
                    GovernanceNotificationOrm.env == _env,
                )
                .first()
            )
            if notify_row is None or notify_row.ticket_id is None:
                return None
            obj = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == notify_row.ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            return GovernanceTicket.from_orm(obj) if obj else None

    # ------------------------------------------------------------------
    # Ticket mutations (write / lifecycle)
    # ------------------------------------------------------------------

    def add_ticket(
        self,
        *,
        ticket_id: str,
        worker_id: str,
        assignee: str,
        bot_id: str,
        owner_id: str,
        dt_version: str,
        initial_decision: str = "actionable",
        current_decision: str = "actionable",
        triggered_dimensions: str | None = None,
        hit_dimensions_count: int | None = None,
        severity: str | None = None,
        estimated_saving_tokens: int | None = None,
        saving_ratio: float | None = None,
        bot_name: str | None = None,
        task_summary: str | None = None,
        notification_structured: str | None = None,
        analysis_status: str | None = None,
        governance_status: str = "open",
        consecutive_normal_days: int = 0,
        remind_at: datetime | None = None,
        remind_count: int = 0,
        last_seen_at: datetime | None = None,
        last_sync_at: datetime | None = None,
        last_decision_dt_version: str | None = None,
    ) -> str:
        """Insert a new ticket row (self-managed session).

        Parameter names use domain terminology; internal mapping writes
        the corresponding ORM column names.

        Flush ensures subsequent ``find_active_ticket()`` can see this
        newly created ticket.

        Returns the ticket_id of the inserted row.
        """
        row = GovernanceTicketOrm(
            ticket_id=ticket_id,
            worker_id=worker_id,
            active_worker=assignee,
            bot_id=bot_id,
            owner_id=owner_id,
            dt_version=dt_version,
            governance_decision=initial_decision,
            latest_decision=current_decision,
            hit_dimensions=triggered_dimensions,
            hit_dimensions_count=hit_dimensions_count,
            governance_max_priority=severity,
            expected_token_saving=estimated_saving_tokens,
            saving_ratio=saving_ratio,
            bot_name=bot_name,
            task_summary=task_summary,
            notification_structured=notification_structured,
            analysis_status=analysis_status,
            governance_status=governance_status,
            consecutive_normal_days=consecutive_normal_days,
            remind_at=remind_at,
            remind_count=remind_count,
            last_seen_at=last_seen_at,
            last_sync_at=last_sync_at,
            last_decision_dt_version=last_decision_dt_version,
        )
        with self._db.orm_session() as s:
            s.add(row)
            s.flush()
            return row.ticket_id

    def accept_feedback(
        self,
        ticket_id: str,
        *,
        user_feedback: str,
        feedback_at: datetime,
        feedback_source: str,
        target_status: str,
        feedback_remark: str | None = None,
        repair_deadline: datetime | None = None,
        resume_at: datetime | None = None,
        review_reason: str | None = None,
        actor_id: str | None = None,
        feedback_payload: str | None = None,
    ) -> bool:
        """Accept user feedback on a ticket (aligned with Protocol signature).

        Args:
            ticket_id: Ticket stable UUID.
            user_feedback: Feedback type (optimized/need_time/dispute/whitelist).
            feedback_at: Feedback timestamp.
            feedback_source: Feedback origin (http_api/card_callback/admin_api).
            target_status: Target governance_status (scheduled/waiting_review).
            feedback_remark: Optional user remark.
            repair_deadline: Repair deadline (need_time required).
            resume_at: Resume time (need_time: repair_deadline + cooldown_days).
            review_reason: Review reason (for non-need_time feedbacks).
            actor_id: Actor who submitted the feedback.
            feedback_payload: Structured feedback JSON.

        Returns True if the ticket was found and updated, False if not found.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            if db_ticket is None:
                return False

            # domain → ORM 列名映射
            db_ticket.response = user_feedback
            db_ticket.response_at = feedback_at
            db_ticket.response_remark = feedback_remark
            db_ticket.response_source = feedback_source
            db_ticket.actor_id = actor_id
            db_ticket.governance_status = target_status

            if feedback_payload is not None:
                db_ticket.feedback_payload = feedback_payload

            if user_feedback == "need_time":
                db_ticket.repair_deadline = repair_deadline
                db_ticket.mute_until = resume_at
            else:
                db_ticket.review_reason = review_reason

            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return True

    def close_ticket(
        self,
        ticket_id: str,
        *,
        close_reason: str,
        closed_at: datetime | None = None,
        cooldown_until: datetime | None = None,
        assignee: str | None = None,
        remind_at: datetime | None = None,
    ) -> bool:
        """Close a ticket (aligned with Protocol: assignee, not active_worker).

        Returns True if the ticket was found and updated, False if not found.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            if db_ticket is None:
                return False

            db_ticket.governance_status = "closed"
            db_ticket.close_reason = close_reason
            db_ticket.closed_at = closed_at or datetime.now()
            db_ticket.remind_at = remind_at

            if cooldown_until is not None:
                db_ticket.cooldown_until = cooldown_until
            # assignee → active_worker (ORM column)
            if assignee is not None:
                db_ticket.active_worker = assignee
            else:
                db_ticket.active_worker = None  # Release on close

            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return True

    def pause_ticket(
        self,
        ticket_id: str,
        *,
        review_reason: str,
        remind_at: datetime | None = None,
    ) -> bool:
        """Pause a ticket to waiting_review (replaces pause / schedule_due session blocks).

        Returns True if the ticket was found and updated, False if not found.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            if db_ticket is None:
                return False

            db_ticket.governance_status = "waiting_review"
            db_ticket.review_reason = review_reason
            db_ticket.remind_at = remind_at

            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return True

    def review_ticket(
        self,
        ticket_id: str,
        *,
        review_decision: str,
        reviewed_by: str,
        reviewed_at: datetime | None = None,
        review_remark: str | None = None,
        close_reason: str | None = None,
        cooldown_until: datetime | None = None,
        remind_at: datetime | None = None,
    ) -> bool:
        """Review a ticket (replaces admin_service review_ticket session block).

        Returns True if the ticket was found and updated, False if not found.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            if db_ticket is None:
                return False

            db_ticket.review_decision = review_decision
            db_ticket.reviewed_by = reviewed_by
            db_ticket.reviewed_at = reviewed_at or datetime.now()
            db_ticket.review_remark = review_remark
            db_ticket.remind_at = remind_at

            if review_decision == "approve_close":
                db_ticket.governance_status = "closed"
                db_ticket.close_reason = close_reason or review_decision
                db_ticket.closed_at = datetime.now()
                db_ticket.active_worker = None
                if cooldown_until is not None:
                    db_ticket.cooldown_until = cooldown_until
            elif review_decision == "approve_whitelist":
                db_ticket.governance_status = "closed"
                db_ticket.close_reason = close_reason or "whitelisted"
                db_ticket.closed_at = datetime.now()
                db_ticket.active_worker = None
            elif review_decision == "reject_for_reopen":
                # Close the ticket (no cooldown) so the next scan cycle
                # creates a fresh open ticket — consistent with the
                # service-layer return value (governance_status="closed",
                # close_reason="review_rejected").
                db_ticket.governance_status = "closed"
                db_ticket.close_reason = close_reason or "review_rejected"
                db_ticket.closed_at = datetime.now()
                db_ticket.active_worker = None
            else:
                db_ticket.governance_status = "closed"
                db_ticket.close_reason = close_reason or review_decision
                db_ticket.closed_at = datetime.now()
                db_ticket.active_worker = None

            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return True

    def refresh_snapshot(
        self,
        ticket_id: str,
        *,
        dt_version: str,
        last_seen_at: datetime | None = None,
        last_sync_at: datetime | None = None,
        bot_name: str | None = None,
        initial_decision: str | None = None,
        triggered_dimensions: str | None = None,
        hit_dimensions_count: int | None = None,
        severity: str | None = None,
        estimated_saving_tokens: int | None = None,
        saving_ratio: float | None = None,
        task_summary: str | None = None,
        notification_structured: str | None = None,
        analysis_status: str | None = None,
        current_decision: str | None = None,
        consecutive_normal_days: int = 0,
        last_decision_dt_version: str | None = None,
        remind_at: datetime | None | object = None,
    ) -> bool:
        """Refresh ticket snapshot from offline batch (aligned with Protocol).

        Parameter names use domain terminology; internal mapping writes
        the corresponding ORM column names.

        Returns True if the ticket was found and updated, False if not found.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            if db_ticket is None:
                return False

            db_ticket.dt_version = dt_version
            if last_seen_at is not None:
                db_ticket.last_seen_at = last_seen_at
            if last_sync_at is not None:
                db_ticket.last_sync_at = last_sync_at
            # domain → ORM 列名映射
            if bot_name is not None:
                db_ticket.bot_name = bot_name
            if initial_decision is not None:
                db_ticket.governance_decision = initial_decision
            if triggered_dimensions is not None:
                db_ticket.hit_dimensions = triggered_dimensions
            if hit_dimensions_count is not None:
                db_ticket.hit_dimensions_count = hit_dimensions_count
            if severity is not None:
                db_ticket.governance_max_priority = severity
            if estimated_saving_tokens is not None:
                db_ticket.expected_token_saving = estimated_saving_tokens
            if saving_ratio is not None:
                db_ticket.saving_ratio = saving_ratio
            if task_summary is not None:
                db_ticket.task_summary = task_summary
            if notification_structured is not None:
                db_ticket.notification_structured = notification_structured
            if analysis_status is not None:
                db_ticket.analysis_status = analysis_status
            if current_decision is not None:
                db_ticket.latest_decision = current_decision
            if consecutive_normal_days is not None:
                db_ticket.consecutive_normal_days = consecutive_normal_days
            if last_decision_dt_version is not None:
                db_ticket.last_decision_dt_version = last_decision_dt_version
            if remind_at is not None and remind_at != "":
                db_ticket.remind_at = remind_at

            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return True

    def advance_reminder(
        self,
        ticket_id: str,
        *,
        remind_at: datetime | None = None,
        is_reminder: bool = False,
        remind_count_delta: int = 0,
    ) -> bool:
        """Advance reminder chain on a ticket (replaces _advance_reminder_chain session).

        Args:
            ticket_id: The ticket to update.
            remind_at: New remind_at value (None = clear).
            is_reminder: If True, increment remind_count by remind_count_delta.
            remind_count_delta: How many to add to remind_count (typically 1).

        Returns True if the ticket was found and updated, False if not found.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            if db_ticket is None:
                return False

            db_ticket.remind_at = remind_at
            if is_reminder and remind_count_delta:
                db_ticket.remind_count = (db_ticket.remind_count or 0) + remind_count_delta

            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return True

    def transition_schedule_due(
        self,
        ticket_id: str,
        *,
        review_reason: str = "schedule_due",
        remind_at: datetime | None = None,
    ) -> bool:
        """Scheduled → waiting_review when mute period expires (§7.3.4).

        Delegates to pause_ticket which sets governance_status=waiting_review.

        Returns True if the ticket was found and updated, False if not found.
        """
        return self.pause_ticket(ticket_id, review_reason=review_reason, remind_at=remind_at)

    def auto_silence_close(
        self,
        ticket_id: str,
        *,
        closed_at: datetime,
        cooldown_until: datetime | None = None,
    ) -> bool:
        """Auto-silence convergence close — consecutive N days normal (§7.2.6).

        Returns True if the ticket was found and updated, False if not found.
        """
        return self.close_ticket(
            ticket_id,
            close_reason="auto_silenced_normal",
            closed_at=closed_at,
            cooldown_until=cooldown_until,
        )

    def bulk_close_open(
        self,
        *,
        close_reason: str,
        closed_at: datetime,
        cooldown_until: datetime | None = None,
    ) -> int:
        """Bulk-close all active open tickets — admin close_all_open.

        Returns the number of rows affected.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            count = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.governance_status.in_(("open", "scheduled")),
                    GovernanceTicketOrm.active_worker.isnot(None),
                    GovernanceTicketOrm.env == _env,
                )
                .update(
                    {
                        GovernanceTicketOrm.governance_status: "closed",
                        GovernanceTicketOrm.close_reason: close_reason,
                        GovernanceTicketOrm.closed_at: closed_at,
                        GovernanceTicketOrm.active_worker: None,
                        **(
                            {GovernanceTicketOrm.cooldown_until: cooldown_until}
                            if cooldown_until is not None
                            else {}
                        ),
                    },
                    synchronize_session="fetch",
                )
            )
            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return count

    def resume_ticket(
        self,
        ticket_id: str,
    ) -> bool:
        """Resume a paused ticket — waiting_review → open.

        Returns True if the ticket was found and updated, False if not found.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            db_ticket = (
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.ticket_id == ticket_id,
                    GovernanceTicketOrm.env == _env,
                )
                .one_or_none()
            )
            if db_ticket is None:
                return False

            db_ticket.governance_status = "open"
            db_ticket.review_reason = None

            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return True

    # ------------------------------------------------------------------
    # Admin delete / count (emergency delete endpoint, §7.5)
    # ------------------------------------------------------------------

    def count_by_dt_versions(
        self, dt_versions: list[str],
    ) -> dict[str, int]:
        """Count rows per dt_version for the given list (env-scoped)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(
                    GovernanceTicketOrm.dt_version,
                    func.count(),
                )
                .filter(
                    GovernanceTicketOrm.dt_version.in_(dt_versions),
                    GovernanceTicketOrm.env == _env,
                )
                .group_by(GovernanceTicketOrm.dt_version)
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
                s.query(GovernanceTicketOrm)
                .filter(
                    GovernanceTicketOrm.dt_version.in_(dt_versions),
                    GovernanceTicketOrm.env == _env,
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
                s.query(GovernanceTicketOrm.id)
                .filter(
                    GovernanceTicketOrm.id.in_(ids),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            existing_ids = {r.id for r in existing}
            not_found = [i for i in ids if i not in existing_ids]

            if existing_ids:
                deleted = (
                    s.query(GovernanceTicketOrm)
                    .filter(GovernanceTicketOrm.id.in_(existing_ids))
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
                s.query(GovernanceTicketOrm.id)
                .filter(
                    GovernanceTicketOrm.id.in_(ids),
                    GovernanceTicketOrm.env == _env,
                )
                .all()
            )
            existing_ids = {r.id for r in existing}
            not_found = [i for i in ids if i not in existing_ids]
            return len(existing_ids), not_found

    # ------------------------------------------------------------------
    # Test seeding (self-managed session + commit)
    # ------------------------------------------------------------------

    def insert_ticket(self, row: GovernanceTicketOrm) -> None:
        """Insert a full-featured ticket row (self-managed session).

        Preserves every column (ticket_id, governance_status, active_worker,
        etc.) so endpoint tests can seed realistic states without bypassing
        the repo layer.
        """
        with self._db.orm_session() as session:
            session.add(row)
            session.flush()