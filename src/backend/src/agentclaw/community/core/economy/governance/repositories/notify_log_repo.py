"""NotifyLog repository — ``ac_governance_notify_log``.

Collects notification-log SELECT/INSERT access that was previously
scattered as raw ``session.query(...)`` across the scan, feedback and
emergency services.

Audit access has been moved to ``GovernanceAuditRepository``
(see ``audit_repo.py``) — one repo per table (R2).

Follows the ``DatabasePlugin`` self-managed session pattern
(see ``harness_patch_record_repository``): each method opens its
own ``orm_session()`` — self-managed sessions; env is resolved
Env is resolved internally via ``get_current_env()``.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from injector import inject

from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceNotifyLog,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

log = get_logger()


class NotifyLogRepository:
    """SELECT / INSERT access to notify_log (self-managed sessions)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # notify_log — single-row lookups (candidate filtering, cycle, feedback)
    # ------------------------------------------------------------------

    def find_by_status(
        self, bot_id: str, owner_id: str, status: str,
    ) -> dict | None:
        """First notify_log for (bot_id, owner_id) at a given governance_status."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.bot_id == bot_id,
                    GovernanceNotifyLog.owner_id == owner_id,
                    GovernanceNotifyLog.governance_status == status,
                    GovernanceNotifyLog.env == _env,
                )
                .first()
            )
            return obj.to_dict() if obj else None

    def find_latest_closed(
        self, bot_id: str, owner_id: str,
    ) -> dict | None:
        """Latest closed notify_log for (bot_id, owner_id) — cooldown check."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.bot_id == bot_id,
                    GovernanceNotifyLog.owner_id == owner_id,
                    GovernanceNotifyLog.governance_status == "closed",
                    GovernanceNotifyLog.env == _env,
                )
                .order_by(GovernanceNotifyLog.closed_at.desc())
                .first()
            )
            return obj.to_dict() if obj else None

    def find_latest(
        self, bot_id: str, owner_id: str,
    ) -> dict | None:
        """Latest notify_log (any status) for (bot_id, owner_id) — cycle inheritance."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.bot_id == bot_id,
                    GovernanceNotifyLog.owner_id == owner_id,
                    GovernanceNotifyLog.env == _env,
                )
                .order_by(GovernanceNotifyLog.gmt_create.desc())
                .first()
            )
            return obj.to_dict() if obj else None

    def get_by_notification_id(
        self, notification_id: str,
    ) -> dict | None:
        """Fetch a notify_log by notification_id."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.notification_id == notification_id,
                    GovernanceNotifyLog.env == _env,
                )
                .first()
            )
            return obj.to_dict() if obj else None

    def get_by_notification_id_and_owner(
        self, notification_id: str, owner_id: str,
    ) -> dict | None:
        """Fetch a notify_log by notification_id scoped to an owner."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            obj = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.notification_id == notification_id,
                    GovernanceNotifyLog.owner_id == owner_id,
                    GovernanceNotifyLog.env == _env,
                )
                .first()
            )
            return obj.to_dict() if obj else None

    # ------------------------------------------------------------------
    # notify_log — list queries (scan steps 7-10, feedback lists)
    # ------------------------------------------------------------------

    def list_by_status(
        self, status: str,
    ) -> list[dict]:
        """All notify_log rows at a single governance_status."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.governance_status == status,
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_by_owner_and_statuses(
        self,
        owner_id: str,
        statuses: list[str],
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        """Owner's notify_log rows in the given statuses, newest first, paged."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.owner_id == owner_id,
                    GovernanceNotifyLog.governance_status.in_(statuses),
                    GovernanceNotifyLog.env == _env,
                )
                .order_by(GovernanceNotifyLog.gmt_create.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_remindable(
        self, now: datetime,
    ) -> list[dict]:
        """Open+sent notifications whose remind_at is due (Step 8)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.governance_status == "open",
                    GovernanceNotifyLog.notify_status == "sent",
                    GovernanceNotifyLog.remind_at <= now,
                    GovernanceNotifyLog.remind_at.isnot(None),
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_expired(
        self, now: datetime,
    ) -> list[dict]:
        """Open notifications past expire_at that were reminded at least once (Step 9)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.governance_status == "open",
                    GovernanceNotifyLog.expire_at <= now,
                    GovernanceNotifyLog.remind_count >= 1,
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_pending_to_cancel(
        self,
    ) -> list[dict]:
        """Pending notifications on closed/expired records — auto-cancel (Step 10a)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.governance_status.in_(["closed", "expired"]),
                    GovernanceNotifyLog.notify_status == "pending",
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_pending_open(
        self,
    ) -> list[dict]:
        """Pending open notifications to send (Step 10b)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.governance_status == "open",
                    GovernanceNotifyLog.notify_status == "pending",
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    # ------------------------------------------------------------------
    # notify_log — emergency-scope queries
    # ------------------------------------------------------------------

    def count_pending(
        self,
    ) -> int:
        """Count open/muted notifications awaiting a response (no response yet)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            return (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.governance_status.in_(["open", "muted"]),
                    GovernanceNotifyLog.response.is_(None),
                    GovernanceNotifyLog.env == _env,
                )
                .count()
            )

    def list_distinct_bot_owner(
        self, bot_ids: list[str],
    ) -> list[tuple[str, str]]:
        """Distinct (bot_id, owner_id) pairs seen for the given bot_ids."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog.bot_id, GovernanceNotifyLog.owner_id)
                .filter(
                    GovernanceNotifyLog.bot_id.in_(bot_ids),
                    GovernanceNotifyLog.env == _env,
                )
                .distinct()
                .all()
            )
            return [(r.bot_id, r.owner_id) for r in rows]

    def list_pending_by_bot_ids(
        self, bot_ids: list[str],
    ) -> list[dict]:
        """Un-responded open/muted notifications for the given bot_ids."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.bot_id.in_(bot_ids),
                    GovernanceNotifyLog.response.is_(None),
                    GovernanceNotifyLog.governance_status.in_(["open", "muted"]),
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_all_pending(
        self,
    ) -> list[dict]:
        """All un-responded open/muted notifications (emergency cancel-all)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.response.is_(None),
                    GovernanceNotifyLog.governance_status.in_(["open", "muted"]),
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def list_open_muted(
        self,
    ) -> list[dict]:
        """All open/muted records regardless of response status (admin close-all)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.governance_status.in_(["open", "muted"]),
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def count_open_muted(
        self,
    ) -> int:
        """Count all open/muted records (regardless of response)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            return (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.governance_status.in_(["open", "muted"]),
                    GovernanceNotifyLog.env == _env,
                )
                .count()
            )

    # ------------------------------------------------------------------
    # Bulk WRITE (self-managed session)
    # ------------------------------------------------------------------

    def recover_sending_timeout(
        self, timeout_minutes: int = 30,
    ) -> int:
        """Revert stale ``sending`` notifies back to ``pending``.

        Called by cron: if a consumer crashed mid-send, the notify stays
        in ``sending``. After ``timeout_minutes``, revert to ``pending``
        so another consumer can pick it up.
        """
        _env = get_current_env()
        cutoff = datetime.now() - timedelta(minutes=timeout_minutes)
        with self._db.orm_session() as s:
            count = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.notify_status == "sending",
                    GovernanceNotifyLog.last_send_at < cutoff,
                    GovernanceNotifyLog.env == _env,
                )
                .update(
                    {GovernanceNotifyLog.notify_status: "pending"},
                    synchronize_session="fetch",
                )
            )
            return count

    def list_pending_for_cron(
        self,
    ) -> list[dict]:
        """All pending notifies for cron to pick up and send."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            rows = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.notify_status == "pending",
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            return [r.to_dict() for r in rows]

    def cancel_pending_by_ticket(
        self, ticket_id: str,
    ) -> int:
        """Cancel all pending notifies for a ticket (on ticket close).

        Returns the number of rows cancelled.
        Does NOT cancel ``sending`` — sending reverts via timeout recovery.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            count = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.ticket_id == ticket_id,
                    GovernanceNotifyLog.notify_status == "pending",
                    GovernanceNotifyLog.env == _env,
                )
                .update(
                    {GovernanceNotifyLog.notify_status: "cancelled"},
                    synchronize_session="fetch",
                )
            )
            return count

    def has_pending_or_sending_reminder(
        self, ticket_id: str,
    ) -> bool:
        """Check if a ticket already has a pending/sending reminder (dedup).

        Used before creating a new reminder notify to avoid duplicates.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            return (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.ticket_id == ticket_id,
                    GovernanceNotifyLog.notify_type == "reminder",
                    GovernanceNotifyLog.notify_status.in_(("pending", "sending")),
                    GovernanceNotifyLog.env == _env,
                )
                .first()
                is not None
            )

    def claim_pending(
        self, notification_id: str, now: datetime,
    ) -> bool:
        """Atomic claim: UPDATE pending → sending for a specific notify.

        Uses ``send_attempt_count += 1`` and ``last_send_at = now``.
        Returns True if the claim succeeded (1 row affected).
        """
        with self._db.orm_session() as s:
            result = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.notification_id == notification_id,
                    GovernanceNotifyLog.notify_status == "pending",
                )
                .update(
                    {
                        GovernanceNotifyLog.notify_status: "sending",
                        GovernanceNotifyLog.send_attempt_count: (
                            GovernanceNotifyLog.send_attempt_count + 1
                        ),
                        GovernanceNotifyLog.last_send_at: now,
                    },
                    synchronize_session="fetch",
                )
            )
            return result == 1

    def mark_sent(
        self,
        notification_id: str,
        external_message_id: str | None,
        sent_at: datetime,
    ) -> bool:
        """Mark a sending notify as sent after successful delivery."""
        with self._db.orm_session() as s:
            result = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.notification_id == notification_id,
                    GovernanceNotifyLog.notify_status == "sending",
                )
                .update(
                    {
                        GovernanceNotifyLog.notify_status: "sent",
                        GovernanceNotifyLog.sent_at: sent_at,
                        GovernanceNotifyLog.external_message_id: external_message_id,
                        GovernanceNotifyLog.last_send_error: None,
                    },
                    synchronize_session="fetch",
                )
            )
            return result == 1

    def mark_send_failed(
        self,
        notification_id: str,
        error_msg: str,
        is_terminal: bool,
    ) -> bool:
        """Mark a sending notify as failed or revert to pending.

        If ``is_terminal`` (max_send_attempts reached) → ``failed``.
        Otherwise → revert to ``pending`` for retry.
        """
        new_status = "failed" if is_terminal else "pending"
        with self._db.orm_session() as s:
            result = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.notification_id == notification_id,
                    GovernanceNotifyLog.notify_status == "sending",
                )
                .update(
                    {
                        GovernanceNotifyLog.notify_status: new_status,
                        GovernanceNotifyLog.last_send_error: error_msg,
                    },
                    synchronize_session="fetch",
                )
            )
            return result == 1

    # ------------------------------------------------------------------
    # Writes — insert (self-managed session)
    # ------------------------------------------------------------------

    def add_notification(self, row: GovernanceNotifyLog) -> str:
        """Insert a new notify_log row (self-managed session).

        ``env`` is auto-filled by the ORM ``default=get_current_env``.

        Flush ensures the row lands in the DB immediately. Without it
        a subsequent query's autoflush could re-INSERT the same pending
        object → ``Duplicate entry`` on UK.
        Returns the notification_id of the inserted row.
        """
        with self._db.orm_session() as s:
            s.add(row)
            s.flush()
            return row.notification_id

    # ------------------------------------------------------------------
    # Test seeding (self-managed session + commit)
    # ------------------------------------------------------------------

    def insert_notification(self, row: GovernanceNotifyLog) -> None:
        """Insert a full-featured notify_log row (self-managed session).

        Unlike ``add_notification`` (also self-managed but returns id),
        this variant exists for backward-compat with endpoint tests
        that seed realistic states through the repo layer.
        """
        with self._db.orm_session() as session:
            session.add(row)
            session.flush()

    # ------------------------------------------------------------------
    # Delete path (admin emergency) — self-managed session
    # ------------------------------------------------------------------

    def delete_by_notification_ids(
        self, notification_ids: list[str],
    ) -> tuple[int, list[str]]:
        """Delete notify_log rows by notification_id list (env-scoped).

        Returns (deleted_count, not_found_ids).
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            existing = (
                s.query(GovernanceNotifyLog.notification_id)
                .filter(
                    GovernanceNotifyLog.notification_id.in_(notification_ids),
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            existing_ids = {r.notification_id for r in existing}
            not_found = [i for i in notification_ids if i not in existing_ids]

            if existing_ids:
                deleted = (
                    s.query(GovernanceNotifyLog)
                    .filter(GovernanceNotifyLog.notification_id.in_(existing_ids))
                    .delete(synchronize_session="fetch")
                )
            else:
                deleted = 0

            return deleted, not_found

    def count_by_notification_ids(
        self, notification_ids: list[str],
    ) -> tuple[int, list[str]]:
        """Count matching rows by notification_id list (env-scoped), no deletion.

        Returns (match_count, not_found_ids).
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            existing = (
                s.query(GovernanceNotifyLog.notification_id)
                .filter(
                    GovernanceNotifyLog.notification_id.in_(notification_ids),
                    GovernanceNotifyLog.env == _env,
                )
                .all()
            )
            existing_ids = {r.notification_id for r in existing}
            not_found = [i for i in notification_ids if i not in existing_ids]
            return len(existing_ids), not_found