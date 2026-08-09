"""NotifyLog repository — ``ac_governance_notify_log``.

Collects notification-log SELECT/INSERT access that was previously
scattered as raw ``session.query(...)`` across the scan, feedback and
admin services.

Audit access has been moved to ``GovernanceAuditRepository``
(see ``audit_repo.py``) — one repo per table (R2).

Follows the ``DatabasePlugin`` self-managed session pattern
(see ``harness_patch_record_repository``): each method opens its
own ``orm_session()`` — self-managed sessions; env is resolved
Env is resolved internally via ``get_current_env()``.
"""
from __future__ import annotations

from datetime import datetime

from injector import inject

from agentclaw.community.core.economy.governance.domain.enums import (
    GovernanceStatus,
    NotifyStatus,
    NotifyType,
)
from agentclaw.community.core.economy.governance.domain.notification import GovernanceNotification
from agentclaw.community.core.economy.governance.orm import GovernanceNotificationOrm, GovernanceTicketOrm
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.protocols.governance import NotifyLogRepositoryProtocol

log = get_logger(__name__)


def _update_ticket_delivery_status(
    session: object, ticket_id: str, status: str,
) -> None:
    """同 session 回写 task_record.delivery_status(投递链路状态变更后调)。

    Args:
        session: 已开启的 ORM session(不自行管理事务,复用调用方的 orm_session)。
        ticket_id: 工单稳定 UUID。
        status: 投递状态单值(pending/sent/failed/cancelled)。
    """
    if not ticket_id:
        return
    session.query(GovernanceTicketOrm).filter(
        GovernanceTicketOrm.ticket_id == ticket_id,
    ).update(
        {GovernanceTicketOrm.delivery_status: status},
        synchronize_session=False,
    )


class NotifyLogRepository(
    NotifyLogRepositoryProtocol,
):
    """SELECT / INSERT access to notify_log (self-managed sessions)."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # notify_log — single-row lookups (candidate filtering, cycle, feedback)
    # ------------------------------------------------------------------

    def get_by_notification_id(
        self, notification_id: str,
    ) -> GovernanceNotification | None:
        """Fetch a notify_log by notification_id — returns domain model."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            obj = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.notification_id == notification_id,
                    GovernanceNotificationOrm.env == _env,
                )
                .first()
            )
            return GovernanceNotification.from_orm(obj) if obj else None

    def get_by_notification_id_and_owner(
        self, notification_id: str, owner_id: str,
    ) -> GovernanceNotification | None:
        """Fetch a notify_log by notification_id scoped to an owner — returns domain model."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            obj = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.notification_id == notification_id,
                    GovernanceNotificationOrm.owner_id == owner_id,
                    GovernanceNotificationOrm.env == _env,
                )
                .first()
            )
            return GovernanceNotification.from_orm(obj) if obj else None

    # ------------------------------------------------------------------
    # notify_log — list queries (scan steps 7-10, feedback lists)
    # ------------------------------------------------------------------

    def list_by_owner_and_statuses(
        self,
        owner_id: str,
        statuses: list[str],
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[GovernanceNotification]:
        """Owner's notify_log rows in the given statuses, newest first, paged — domain models."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            rows = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.owner_id == owner_id,
                    GovernanceNotificationOrm.governance_status.in_(statuses),
                    GovernanceNotificationOrm.env == _env,
                )
                .order_by(GovernanceNotificationOrm.gmt_create.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [GovernanceNotification.from_orm(r) for r in rows]

    def list_by_ticket(
        self,
        ticket_id: str,
        *,
        only_pending: bool = False,
    ) -> list[GovernanceNotification]:
        """A ticket's notify_log rows (all notify types), newest first — domain models.

        Args:
            ticket_id: 工单稳定 UUID。
            only_pending: True → 仅 pending/sending(待回复)通知。

        Returns:
            该 ticket 关联的通知列表(notification_id/notify_status/...)。
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            q = s.query(GovernanceNotificationOrm).filter(
                GovernanceNotificationOrm.ticket_id == ticket_id,
                GovernanceNotificationOrm.env == _env,
            )
            if only_pending:
                q = q.filter(
                    GovernanceNotificationOrm.notify_status.in_(("pending", "sending")),
                )
            rows = q.order_by(GovernanceNotificationOrm.gmt_create.desc()).all()
            return [GovernanceNotification.from_orm(r) for r in rows]

    # ------------------------------------------------------------------
    # notify_log — admin-scope queries
    # ------------------------------------------------------------------

    def count_pending(
        self,
    ) -> int:
        """Count open/muted notifications awaiting a response (no response yet)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            return (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.governance_status.in_([GovernanceStatus.OPEN, "muted"]),
                    GovernanceNotificationOrm.response.is_(None),
                    GovernanceNotificationOrm.env == _env,
                )
                .count()
            )

    def list_distinct_bot_owner(
        self, bot_ids: list[str],
    ) -> list[tuple[str, str]]:
        """Distinct (bot_id, owner_id) pairs seen for the given bot_ids."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            rows = (
                s.query(GovernanceNotificationOrm.bot_id, GovernanceNotificationOrm.owner_id)
                .filter(
                    GovernanceNotificationOrm.bot_id.in_(bot_ids),
                    GovernanceNotificationOrm.env == _env,
                )
                .distinct()
                .all()
            )
            return [(r.bot_id, r.owner_id) for r in rows]

    def list_ticket_ids_open_muted(
        self, *, only_unresponded: bool = False,
    ) -> list[str]:
        """Distinct non-None ``ticket_id`` set for open/muted notifications.

        Mirrors the filter of :meth:`bulk_close_open_muted` so the caller can
        pre-collect the affected ticket set (Task 8 aligns ticket/notify sets).
        ``only_unresponded=True`` adds ``response IS NULL`` (cancel_pending
        scope); ``False`` is the close_all_open scope. Duplicates de-duped;
        ``None`` ticket_ids (nullable at ORM level but non-None at creation)
        filtered out.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            q = (
                s.query(GovernanceNotificationOrm.ticket_id)
                .filter(
                    GovernanceNotificationOrm.governance_status.in_(
                        [GovernanceStatus.OPEN, "muted"],
                    ),
                    GovernanceNotificationOrm.env == _env,
                    GovernanceNotificationOrm.ticket_id.isnot(None),
                )
            )
            if only_unresponded:
                q = q.filter(GovernanceNotificationOrm.response.is_(None))
            rows = q.distinct().all()
            return [r.ticket_id for r in rows]

    def list_ticket_ids_by_bots(
        self, bot_ids: list[str],
    ) -> list[str]:
        """Distinct non-None ``ticket_id`` set for the given bots (unresponded).

        Mirrors the filter of :meth:`bulk_cancel_by_bots` (``bot_id IN (...)``
        + ``response IS NULL`` + open/muted) so bulk_whitelist can pre-collect
        the affected ticket set. ``None`` ticket_ids filtered out.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            rows = (
                s.query(GovernanceNotificationOrm.ticket_id)
                .filter(
                    GovernanceNotificationOrm.bot_id.in_(bot_ids),
                    GovernanceNotificationOrm.response.is_(None),
                    GovernanceNotificationOrm.governance_status.in_(
                        [GovernanceStatus.OPEN, "muted"],
                    ),
                    GovernanceNotificationOrm.env == _env,
                    GovernanceNotificationOrm.ticket_id.isnot(None),
                )
                .distinct()
                .all()
            )
            return [r.ticket_id for r in rows]

    def count_open_muted(
        self,
    ) -> int:
        """Count all open/muted records (regardless of response)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            return (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.governance_status.in_([GovernanceStatus.OPEN, "muted"]),
                    GovernanceNotificationOrm.env == _env,
                )
                .count()
            )

    # ------------------------------------------------------------------
    # Bulk WRITE (self-managed session)
    # ------------------------------------------------------------------

    def list_pending_for_cron(
        self,
    ) -> list[GovernanceNotification]:
        """All pending notifies for cron to pick up and send — returns domain models."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            rows = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.notify_status == NotifyStatus.PENDING,
                    GovernanceNotificationOrm.env == _env,
                )
                .all()
            )
            return [GovernanceNotification.from_orm(r) for r in rows]

    def list_pending_by_worker(
        self,
        worker_id: str,
    ) -> list[GovernanceNotification]:
        """Pending notifies scoped to one worker (owner_id:bot_id) — for deliver_by_worker.

        Unlike :meth:`list_pending_for_cron` (全量),本方法按 worker 精准过滤,
        供 admin ``tickets:deliver`` 端点用(不重跑状态机,pending 已躺 notify_log)。

        Args:
            worker_id: ``owner_id:bot_id`` 复合键,与 GovernanceNotification.worker_id 对齐。

        Returns:
            该 worker 的 pending 通知领域模型列表(按 gmt_create 升序,与 cron 一致)。
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            rows = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.worker_id == worker_id,
                    GovernanceNotificationOrm.notify_status == NotifyStatus.PENDING,
                    GovernanceNotificationOrm.env == _env,
                )
                .order_by(GovernanceNotificationOrm.gmt_create.asc())
                .all()
            )
            return [GovernanceNotification.from_orm(r) for r in rows]

    def cancel_pending_by_ticket(
        self, ticket_id: str,
    ) -> int:
        """Cancel all pending notifies for a ticket (on ticket close).

        Returns the number of rows cancelled.
        Does NOT cancel ``sending`` — sending reverts via timeout recovery.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            count = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.ticket_id == ticket_id,
                    GovernanceNotificationOrm.notify_status == NotifyStatus.PENDING,
                    GovernanceNotificationOrm.env == _env,
                )
                .update(
                    {GovernanceNotificationOrm.notify_status: NotifyStatus.CANCELLED},
                    synchronize_session="fetch",
                )
            )
            if count > 0:
                _update_ticket_delivery_status(
                    s, ticket_id,
                    NotifyStatus.CANCELLED.value,
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
            s.expire_on_commit = False
            return (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.ticket_id == ticket_id,
                    GovernanceNotificationOrm.notify_type == NotifyType.REMINDER,
                    GovernanceNotificationOrm.notify_status.in_((NotifyStatus.PENDING, NotifyStatus.SENDING)),
                    GovernanceNotificationOrm.env == _env,
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
            s.expire_on_commit = False
            result = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.notification_id == notification_id,
                    GovernanceNotificationOrm.notify_status == NotifyStatus.PENDING,
                )
                .update(
                    {
                        GovernanceNotificationOrm.notify_status: NotifyStatus.SENDING,
                        GovernanceNotificationOrm.send_attempt_count: (
                            GovernanceNotificationOrm.send_attempt_count + 1
                        ),
                        GovernanceNotificationOrm.last_send_at: now,
                    },
                    synchronize_session="fetch",
                )
            )
            if result == 1:
                row = s.query(GovernanceNotificationOrm).filter(
                    GovernanceNotificationOrm.notification_id == notification_id,
                ).first()
                if row and row.ticket_id:
                    _update_ticket_delivery_status(
                        s, row.ticket_id,
                        NotifyStatus.PENDING.value,  # sending → pending (投递中仍属待发送态)
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
            s.expire_on_commit = False
            result = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.notification_id == notification_id,
                    GovernanceNotificationOrm.notify_status == NotifyStatus.SENDING,
                )
                .update(
                    {
                        GovernanceNotificationOrm.notify_status: NotifyStatus.SENT,
                        GovernanceNotificationOrm.sent_at: sent_at,
                        GovernanceNotificationOrm.external_message_id: external_message_id,
                        GovernanceNotificationOrm.last_send_error: None,
                    },
                    synchronize_session="fetch",
                )
            )
            if result == 1:
                row = s.query(GovernanceNotificationOrm).filter(
                    GovernanceNotificationOrm.notification_id == notification_id,
                ).first()
                if row and row.ticket_id:
                    _update_ticket_delivery_status(
                        s, row.ticket_id,
                        NotifyStatus.SENT.value,  # 投递成功
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
        new_status = NotifyStatus.FAILED if is_terminal else NotifyStatus.PENDING
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            result = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.notification_id == notification_id,
                    GovernanceNotificationOrm.notify_status == NotifyStatus.SENDING,
                )
                .update(
                    {
                        GovernanceNotificationOrm.notify_status: new_status,
                        GovernanceNotificationOrm.last_send_error: error_msg,
                    },
                    synchronize_session="fetch",
                )
            )
            if result == 1:
                row = s.query(GovernanceNotificationOrm).filter(
                    GovernanceNotificationOrm.notification_id == notification_id,
                ).first()
                if row and row.ticket_id:
                    fail_status = NotifyStatus.FAILED.value if is_terminal else NotifyStatus.PENDING.value
                    _update_ticket_delivery_status(
                        s, row.ticket_id,
                        fail_status,  # 终态失败/回退待发
                    )
            return result == 1

    # ------------------------------------------------------------------
    # Writes — insert (self-managed session)
    # ------------------------------------------------------------------

    def add_notification(self, notification: GovernanceNotification) -> str:
        """Insert a new notify_log row (self-managed session).

        Accepts domain model, translates to ORM via ``to_orm()``.
        ``env`` is auto-filled by the ORM ``default=get_current_env``.

        Returns the notification_id of the inserted row.
        """
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            orm_row = notification.to_orm()
            s.add(orm_row)
            s.flush()
            return orm_row.notification_id

    def save_notification(self, notification: GovernanceNotification) -> bool:
        """Persist a (mutated) domain notification back to its row.

        通知发送状态机领域往返的写回原语(对齐 ``save_ticket`` 范式):
        按 ``notification_id`` + env 查出 ORM 行 → ``apply_to`` 只写可变投递态
        (notify_status / notify_channel / send_attempt_count / last_send_at /
        last_send_error / external_message_id / sent_at)→ commit。冻结快照/sealed
        列不动。

        调用方(``NotifyLifecycleService``)在调本原语前已 invoke 领域守卫方法
        (``mark_claimed`` / ``mark_sent`` / ``mark_failed``);本原语只写回,
        repo 持无状态转移语义命令。Returns True if found+updated, False if not found.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            db_row = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.notification_id == notification.notification_id,
                    GovernanceNotificationOrm.env == _env,
                )
                .one_or_none()
            )
            if db_row is None:
                return False
            notification.apply_to(db_row)
            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
            return True

    # ------------------------------------------------------------------
    # Test seeding (self-managed session + commit)
    # ------------------------------------------------------------------

    def insert_notification(self, row: GovernanceNotificationOrm) -> None:
        """Insert a full-featured notify_log row (self-managed session).

        Unlike ``add_notification`` (also self-managed but returns id),
        this variant exists for backward-compat with endpoint tests
        that seed realistic states through the repo layer.
        """
        with self._db.orm_session() as session:
            session.expire_on_commit = False
            session.add(row)
            session.flush()

    # ------------------------------------------------------------------
    # Delete path (admin) — self-managed session
    # ------------------------------------------------------------------

    def delete_by_notification_ids(
        self, notification_ids: list[str],
    ) -> tuple[int, list[str]]:
        """Delete notify_log rows by notification_id list (env-scoped).

        Returns (deleted_count, not_found_ids).
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            existing = (
                s.query(GovernanceNotificationOrm.notification_id)
                .filter(
                    GovernanceNotificationOrm.notification_id.in_(notification_ids),
                    GovernanceNotificationOrm.env == _env,
                )
                .all()
            )
            existing_ids = {r.notification_id for r in existing}
            not_found = [i for i in notification_ids if i not in existing_ids]

            if existing_ids:
                deleted = (
                    s.query(GovernanceNotificationOrm)
                    .filter(GovernanceNotificationOrm.notification_id.in_(existing_ids))
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
            s.expire_on_commit = False
            existing = (
                s.query(GovernanceNotificationOrm.notification_id)
                .filter(
                    GovernanceNotificationOrm.notification_id.in_(notification_ids),
                    GovernanceNotificationOrm.env == _env,
                )
                .all()
            )
            existing_ids = {r.notification_id for r in existing}
            not_found = [i for i in notification_ids if i not in existing_ids]
            return len(existing_ids), not_found

    def count_by_ticket_id(self, ticket_id: str) -> int:
        """Count notify_log rows belonging to a ticket_id (env-scoped).

        Used by ticket-cascade dry-run preview. Returns the number of
        notification rows that share this ticket_id within the current env.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            return (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.ticket_id == ticket_id,
                    GovernanceNotificationOrm.env == _env,
                )
                .count()
            )

    def delete_by_ticket_id(self, ticket_id: str) -> int:
        """Delete all notify_log rows belonging to a ticket_id (env-scoped).

        Single-SQL bulk delete (`WHERE env=? AND ticket_id=?`, backed by
        ``idx_econ_gov_notify_ticket_id``). Returns the number of rows
        deleted. Used by ticket-cascade best-effort notify cleanup.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            deleted = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.ticket_id == ticket_id,
                    GovernanceNotificationOrm.env == _env,
                )
                .delete(synchronize_session=False)
            )
            return deleted

    # ------------------------------------------------------------------
    # Command methods (P4 — Service→Repo ORM relocation)
    # ------------------------------------------------------------------

    def bulk_close_open_muted(
        self,
        *,
        close_reason: str,
        closed_at: datetime,
        cooldown_until: datetime | None = None,
        only_unresponded: bool = False,
    ) -> int:
        """批量关闭 open/muted 通知 — admin cancel_pending / close_all_open。

        Args:
            close_reason: Business close reason (CloseReason value).
            closed_at: Close timestamp.
            cooldown_until: Cooldown expiry.
            only_unresponded: True = only where response IS NULL (cancel_pending);
                False = all open/muted (close_all_open).

        Returns:
            Number of rows updated.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            q = s.query(GovernanceNotificationOrm).filter(
                GovernanceNotificationOrm.governance_status.in_(
                    [GovernanceStatus.OPEN, "muted"],
                ),
                GovernanceNotificationOrm.env == _env,
            )
            if only_unresponded:
                q = q.filter(GovernanceNotificationOrm.response.is_(None))

            affected = q.all()
            updated = 0
            for row in affected:
                if only_unresponded or row.notify_status == NotifyStatus.PENDING:
                    row.notify_status = NotifyStatus.CANCELLED
                row.governance_status = GovernanceStatus.CLOSED
                row.close_reason = close_reason
                row.closed_at = closed_at
                row.cooldown_until = cooldown_until
                updated += 1
            return updated

    def bulk_cancel_by_bots(
        self,
        bot_ids: list[str],
        *,
        close_reason: str,
        closed_at: datetime,
        cooldown_until: datetime | None = None,
    ) -> int:
        """批量取消指定 bot 的 open/muted 通知 — whitelist bulk_whitelist。

        Returns the number of rows updated.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            affected = (
                s.query(GovernanceNotificationOrm)
                .filter(
                    GovernanceNotificationOrm.bot_id.in_(bot_ids),
                    GovernanceNotificationOrm.response.is_(None),
                    GovernanceNotificationOrm.governance_status.in_(
                        [GovernanceStatus.OPEN, "muted"],
                    ),
                    # 状态守卫:只取消待发通知,不动已投递(sent/failed/sending)。
                    GovernanceNotificationOrm.notify_status == NotifyStatus.PENDING,
                    GovernanceNotificationOrm.env == _env,
                )
                .all()
            )
            for row in affected:
                row.notify_status = NotifyStatus.CANCELLED
                row.governance_status = GovernanceStatus.CLOSED
                row.close_reason = close_reason
                row.closed_at = closed_at
                row.cooldown_until = cooldown_until
            return len(affected)

    def update_delivery_status(
        self,
        notification_id: str,
        *,
        status: NotifyStatus,
        external_id: str | None = None,
        error: str | None = None,
        at: datetime | None = None,
        increment_attempt: bool = False,
        channel: str | None = None,
    ) -> bool:
        """投递状态变更 — 合并 claim / mark_sent / mark_failed。

        claim:       update_delivery_status(id, status=SENDING, at=now, increment_attempt=True)
        mark_sent:   update_delivery_status(id, status=SENT, external_id=ext_id, at=now)
        mark_failed: update_delivery_status(id, status=FAILED, error=msg)
                     update_delivery_status(id, status=PENDING, error=msg)  # non-terminal
        """
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            update_values: dict = {
                GovernanceNotificationOrm.notify_status: status,
            }
            if external_id is not None:
                update_values[GovernanceNotificationOrm.external_message_id] = external_id
            if error is not None:
                update_values[GovernanceNotificationOrm.last_send_error] = error
            if at is not None:
                update_values[GovernanceNotificationOrm.last_send_at] = at
                if status == NotifyStatus.SENT:
                    update_values[GovernanceNotificationOrm.sent_at] = at
            if increment_attempt:
                update_values[GovernanceNotificationOrm.send_attempt_count] = (
                    GovernanceNotificationOrm.send_attempt_count + 1
                )
            if channel is not None:
                update_values[GovernanceNotificationOrm.notify_channel] = channel

            count = s.query(GovernanceNotificationOrm).filter(
                GovernanceNotificationOrm.notification_id == notification_id,
            ).update(update_values, synchronize_session="fetch")
            return count > 0