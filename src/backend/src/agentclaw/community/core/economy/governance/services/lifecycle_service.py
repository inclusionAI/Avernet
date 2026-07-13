"""[内核] GovernanceLifecycleService — sole driver of the ticket main state machine.

This is the **heavy orchestration layer** for the governance ticket lifecycle
(open / scheduled / waiting_review / closed). It is the only component the
three entry channels (offline-batch / cron tick / ticket review) invoke to
mutate ticket state; they cease to touch ``governance_status`` directly.

Linkage, per the spec's two-machine boundary:
  - Ticket main state machine — driven here (this service).
  - Notify-delivery state machine (pending→sending→sent/failed/cancelled) —
    NOT driven here; on ticket lifecycle change this service orchestrates a
    one-way ``cancel_pending_by_ticket`` side effect on the notify side
    (ticket is the cause, notify is the effect; notify never drives ticket).

Persistence (方案 A — guard in service, repo degrades to primitives):
the service is the **sole component that advances ticket state**. Each
transition method loads a detached domain model via
``task_repo.find_by_ticket_id``, invokes a ``GovernanceTicket``
state-machine method (the white-list guard activates HERE and raises
``IllegalTicketTransitionError`` on a non-whitelisted transition), then
persists via ``task_repo.save_ticket`` (lifecycle, ``apply_to``) or
``_save_ticket_with_snapshot`` (snapshot path, ``to_orm``). The repo
holds **no semantic transition command** — so "sole driver" is enforced
by layering, not caller discipline. This service composes persistence
with side effects:

  1. find → model state-machine method (guard raises
     ``IllegalTicketTransitionError``),
  2. ``save_ticket`` / ``_save_ticket_with_snapshot``
     (skipped if the guard raised — illegal transition never persists),
  3. orchestrate side effects — cancel pending notifications / add whitelist,
  4. write an audit row.

Illegal transitions are caught, audited, and surfaced as a ``False`` return
(or 0 for bulk) — never propagated as an HTTP 500.

Note: ``GovernanceLifecycleService`` structurally satisfies
``GovernanceLifecycleServiceProtocol`` (api layer). It is a *service*,
not a ``plugin_api`` ``Plugin`` — conformance is pinned by the contract
suite + grep guard, not ``test_protocol_contracts.py``.
"""
from __future__ import annotations

from datetime import datetime

from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    CloseReason,
    GovernanceStatus,
)
from agentclaw.community.core.economy.governance.domain.ticket import (
    GovernanceTicket,
    IllegalTicketTransitionError,
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
from agentclaw.community.log import get_logger
from injector import inject


log = get_logger(__name__)


class GovernanceLifecycleService:
    """Sole driver of the governance ticket main state machine (Rule 14/20).

    Note: this service deliberately does NOT depend on
    ``GovernanceWhitelistService`` — the whitelist-add side effect of user
    feedback (``accept_feedback`` user_feedback=whitelist) is owned by the
    caller (``feedback_service``), and the bulk-whitelist ticket-close
    orchestration (``whitelist_service.bulk_whitelist``) calls back into this
    driver. Keeping whitelist_service out of this constructor breaks the
    whitelist↔lifecycle DI cycle.
    """

    @inject
    def __init__(
        self,
        task_repo: TaskRecordRepository,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
    ) -> None:
        self._task_repo = task_repo
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo

    # ── private helpers ────────────────────────────────────────────────

    def _cancel_pending(self, ticket_id: str | None) -> int:
        """Best-effort cancel of pending notifies for a ticket (one-way side
        effect on the notify-delivery machine). Never raises."""
        if not ticket_id:
            return 0
        try:
            return self._notify_repo.cancel_pending_by_ticket(ticket_id)
        except Exception as exc:  # noqa: BLE001 — side effect must not break the transition
            log.warning("[Lifecycle] cancel_pending_by_ticket failed for %s: %s", ticket_id, exc)
            return 0

    def _audit_illegal(
        self, ticket_id: str, action: str, exc: Exception,
    ) -> None:
        """Record an illegal-transition attempt as an audit row (never raises)."""
        try:
            self._audit_repo.add_audit(
                f"lifecycle-illegal-{action}",
                action_taken=action,
                source="lifecycle_service",
                error_msg=f"ticket_id={ticket_id}; rejected={exc}",
                dry_run=0,
            )
        except Exception:  # noqa: BLE001 — audit must not break the call
            pass

    # ── Entry: offline-batch (record_process_service) ──────────────────

    def open_ticket(self, *, ticket: GovernanceTicket) -> str:
        """New ticket → OPEN (already OPEN on create): persist.

        Persists the row only — the caller (record_process_service) owns the
        ENQUEUED audit row (carries the batch run_id + record-cohort fields),
        so the driver does not double-write it. Matches the audit-ownership
        convention of all siblings (refresh/close/transition/pause/review).

        Args:
            ticket: ``GovernanceTicket`` domain model built by the caller.

        Returns:
            The persisted ``ticket_id``.
        """
        # TODO(Task 5): once add_ticket accepts a domain model, replace the
        # scalar extraction with a direct ``self._task_repo.add_ticket(ticket=ticket)``.
        snapshot = ticket.snapshot
        ticket_id = self._task_repo.add_ticket(
            ticket_id=ticket.ticket_id or "",
            worker_id=ticket.worker_id,
            assignee=ticket.assignee or ticket.worker_id,
            bot_id=ticket.bot_id or "",
            owner_id=ticket.owner_id or "",
            dt_version=snapshot.dt_version,
            initial_decision=snapshot.initial_decision,
            current_decision=snapshot.current_decision or "actionable",
            triggered_dimensions=snapshot.triggered_dimensions,
            hit_dimensions_count=snapshot.hit_dimensions_count,
            severity=snapshot.severity,
            estimated_saving_tokens=snapshot.estimated_saving_tokens,
            saving_ratio=snapshot.saving_ratio,
            bot_name=ticket.bot_name,
            task_summary=snapshot.task_summary,
            notification_structured=snapshot.notification_structured,
            analysis_status=snapshot.analysis_status,
            governance_status=ticket.governance_status.value,
            consecutive_normal_days=snapshot.consecutive_normal_days,
            remind_at=ticket.remind_at,
            remind_count=ticket.remind_count,
            last_seen_at=snapshot.last_seen_at,
            last_sync_at=snapshot.last_sync_at,
            last_decision_dt_version=snapshot.last_decision_dt_version,
        )
        return ticket_id

    def refresh_snapshot(self, ticket_id: str, **snapshot_fields: object) -> bool:
        """Refresh an active ticket's mutable snapshot (non-state-transition).

        方案 A 链路:find → 模型 ``refresh_snapshot(**fields)`` →
        ``_save_ticket_with_snapshot``(to_orm,写快照)。状态不变。

        Two non-snapshot passthrough kwargs carry existing repo semantics
        (kept verbatim so the offline-batch migration is behavior-preserving):
          - ``bot_name`` — identity column (not on MutableSnapshot); set on
            the model so ``to_orm`` writes it, mirroring repo's direct
            ``db_ticket.bot_name`` write. Only overwrites when non-None.
          - ``remind_at`` — lifecycle sentinel: ``""`` = don't touch
            (default), ``datetime`` = set, ``None`` = clear. Lives on the
            model (not the snapshot); set before ``to_orm`` so it persists.

        Returns True if the ticket was found and updated.
        """
        # Pop non-snapshot passthroughs before delegating to the model's
        # snapshot-replace (which rejects keys absent from MutableSnapshot).
        bot_name = snapshot_fields.pop("bot_name", None)
        remind_at = snapshot_fields.pop("remind_at", "")
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        ticket.refresh_snapshot(**snapshot_fields)
        if bot_name is not None:
            ticket.bot_name = bot_name
        if remind_at != "":  # type: ignore[comparison-overlap]
            ticket.remind_at = remind_at  # type: ignore[assignment]
        return self._task_repo._save_ticket_with_snapshot(ticket)  # noqa: SLF001 — primitive

    def close_for_whitelist_hit(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """Whitelist hit → CLOSED(whitelist_filtered) + cancel pending + audit.

        方案 A 链路:find → ``ticket.close()``(守卫激活)→ ``save_ticket`` →
        取消通知。非法转移被守卫抛出,驱动服务捕获转审计 + False。

        Returns True if the ticket was found and closed, False if not found.
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        try:
            ticket.close(
                close_reason=CloseReason.WHITELIST_FILTERED, closed_at=now,
            )
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "close_for_whitelist_hit", exc)
            return False
        if not self._task_repo.save_ticket(ticket):
            return False
        self._cancel_pending(ticket_id)
        return True

    # ── Entry: cron tick (scan_service) ────────────────────────────────

    def transition_schedule_due(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """SCHEDULED → WAITING_REVIEW + cancel pending.

        方案 A 链路:find → ``ticket.pause(review_reason='schedule_due')``
        (守卫激活,清 remind_at)→ ``save_ticket`` → 取消通知。

        Returns True if the ticket was found and transitioned, False if not found.
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        try:
            ticket.pause(review_reason="schedule_due")
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "transition_schedule_due", exc)
            return False
        if not self._task_repo.save_ticket(ticket):
            return False
        self._cancel_pending(ticket_id)
        return True

    def auto_silence_close(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """OPEN → CLOSED(auto_silenced_normal) on consecutive-normal convergence.

        方案 A 链路:find → ``ticket.close(close_reason=AUTO_SILENCED_NORMAL)``
        (守卫激活)→ ``save_ticket`` → 取消通知。

        Returns True if the ticket was found and closed, False if not found.
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        try:
            ticket.close(
                close_reason=CloseReason.AUTO_SILENCED_NORMAL, closed_at=now,
            )
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "auto_silence_close", exc)
            return False
        if not self._task_repo.save_ticket(ticket):
            return False
        self._cancel_pending(ticket_id)
        return True

    def advance_reminder(
        self,
        ticket_id: str,
        *,
        remind_at: datetime | None,
        is_reminder: bool = False,
        remind_count_delta: int = 0,
    ) -> bool:
        """Advance the reminder chain on a ticket (non-state-transition).

        方案 A 链路:find → 设 ``ticket.remind_at`` / 增 ``ticket.remind_count``
        → ``save_ticket``(apply_to 写生命周期)。

        Returns True if the ticket was found and updated, False if not found.
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        ticket.remind_at = remind_at
        if is_reminder and remind_count_delta:
            ticket.remind_count = (ticket.remind_count or 0) + remind_count_delta
        return self._task_repo.save_ticket(ticket)

    # ── Entry: ticket review (feedback_service / admin_service) ────────

    def accept_feedback(
        self,
        ticket_id: str,
        *,
        user_feedback: str,
        feedback_at: datetime,
        feedback_source: str,
        target_status: GovernanceStatus,
        feedback_remark: str | None = None,
        repair_deadline: datetime | None = None,
        resume_at: datetime | None = None,
        review_reason: str | None = None,
        actor_id: str | None = None,
        feedback_payload: str | None = None,
    ) -> bool:
        """Accept user feedback → OPEN → WAITING_REVIEW/SCHEDULED + cancel
        pending.

        方案 A 链路:find → ``ticket.accept_feedback(...)``(守卫激活,
        清 remind_at)→ ``save_ticket`` → 取消通知。

        The whitelist-add side effect for ``user_feedback="whitelist"`` is
        owned by the caller (``feedback_service.resolve``), NOT this driver —
        keeping whitelist_service out of this constructor breaks the
        whitelist↔lifecycle DI cycle. Returns True if found.
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        try:
            ticket.accept_feedback(
                user_feedback=user_feedback,
                feedback_at=feedback_at,
                feedback_source=feedback_source,
                target_status=target_status,
                feedback_remark=feedback_remark,
                repair_deadline=repair_deadline,
                resume_at=resume_at,
                review_reason=review_reason,
                actor_id=actor_id,
                feedback_payload=feedback_payload,
            )
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "accept_feedback", exc)
            return False
        if not self._task_repo.save_ticket(ticket):
            return False
        self._cancel_pending(ticket_id)
        return True

    def pause_ticket(self, ticket_id: str, *, review_reason: str) -> bool:
        """OPEN/SCHEDULED → WAITING_REVIEW + cancel pending.

        方案 A 链路:find → ``ticket.pause(review_reason)``(守卫激活,
        清 remind_at)→ ``save_ticket`` → 取消通知。

        Returns True if found, False if not found.
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        try:
            ticket.pause(review_reason=review_reason)
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "pause_ticket", exc)
            return False
        if not self._task_repo.save_ticket(ticket):
            return False
        self._cancel_pending(ticket_id)
        return True

    def resume_ticket(self, ticket_id: str) -> bool:
        """WAITING_REVIEW → OPEN.  # no caller yet — kept for symmetry.

        方案 A 链路:find → ``ticket.resume()``(守卫激活)→ 清 review_reason
        → ``save_ticket``。

        Returns True if found, False if not found.
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        try:
            ticket.resume()
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "resume_ticket", exc)
            return False
        ticket.review_reason = None  # clear stale pause reason
        return self._task_repo.save_ticket(ticket)

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
    ) -> bool:
        """WAITING_REVIEW → CLOSED (three-branch) + cancel pending.

        方案 A 链路:find → ``ticket.review(...)``(守卫激活,三态分支,
        清 active_worker + remind_at)→ ``save_ticket`` → 取消通知。

        Returns True if found, False if not found.
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        try:
            ticket.review(
                review_decision=review_decision,
                reviewed_by=reviewed_by,
                reviewed_at=reviewed_at,
                review_remark=review_remark,
                close_reason=close_reason,
                cooldown_until=cooldown_until,
            )
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "review_ticket", exc)
            return False
        if not self._task_repo.save_ticket(ticket):
            return False
        self._cancel_pending(ticket_id)
        return True

    def emergency_close(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """Any non-CLOSED → CLOSED(emergency_closed) + cancel pending.

        方案 A 链路:find → 幂等检查(已 CLOSED 返 False)→
        ``ticket.close(EMERGENCY_CLOSED)``(守卫激活)→ ``save_ticket`` →
        取消通知。审计由调用方(admin_service)拥有(携带 reason +
        actor_id=admin_id)—— 与 pause_ticket / review_ticket 等
        sibling 方法一致,driver 不重复写审计。

        Returns True if the ticket was found and closed, False if not found
        (or already closed — idempotent no-op).
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        if ticket.governance_status == GovernanceStatus.CLOSED:
            return False  # already closed — idempotent no-op
        try:
            ticket.close(
                close_reason=CloseReason.EMERGENCY_CLOSED, closed_at=now,
            )
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "emergency_close", exc)
            return False
        if not self._task_repo.save_ticket(ticket):
            return False
        self._cancel_pending(ticket_id)
        return True

    def bulk_close_open(self, *, close_reason: str, now: datetime) -> int:
        """Bulk emergency-close all open/scheduled tickets — joint orchestration:
        land ``task_record`` CLOSED (ticket machine, via the bulk primitive)
        + cancel pending notifies (notify-delivery machine, one-way side
        effect). Ticket is cause, notify is effect.

        Per-spec exemption: the bulk primitive (``repo.bulk_close_open``)
        bypasses the per-row model load for performance; state legality is
        enforced by the SQL ``WHERE status IN (open,scheduled)`` predicate.

        Returns the number of tickets closed.
        """
        count = self._task_repo.bulk_close_open(
            close_reason=close_reason, closed_at=now,
        )
        # Best-effort cancel of pending notifies across all closed tickets.
        # The notify bulk-cancel by status is orchestrated in admin_service /
        # whitelist_service (Task 8 aligns the ticket/notify sets); here we
        # only guarantee the ticket side lands.
        try:
            self._audit_repo.add_audit(
                "lifecycle-bulk-close-open",
                action_taken=AuditAction.ADMIN_CLOSE_ALL,
                source="admin_api",
                error_msg=f"bulk_closed={count}; reason={close_reason}",
                dry_run=0,
            )
        except Exception:  # noqa: BLE001
            pass
        return count

    def bulk_close_by_ticket_ids(
        self, ticket_ids: list[str], *, now: datetime,
    ) -> int:
        """Per-ticket emergency-close by ``ticket_id`` set — Task 8 用:
        cancel_pending / bulk_whitelist 取消通知投递后,按被关通知的
        ``ticket_id`` 集合关对应 ``task_record`` 主体,口径对齐通知侧。

        逐条走 :meth:`emergency_close` 链路(find→守卫激活→save→
        cancel_pending),激活领域模型白名单——**不裸用全量**
        :meth:`bulk_close_open`(会多关已反馈的 scheduled 单)。
        幂等:已 CLOSED / not-found / 非法态均返回 False 且不计数。

        Args:
            ticket_ids: 被取消通知对应的 ticket_id 集合(已剔 None)。
            now: 关闭时间戳。

        Returns:
            实际关闭的工单数(不含幂等跳过的)。
        """
        closed = 0
        for ticket_id in ticket_ids:
            if self.emergency_close(ticket_id, now=now):
                closed += 1
        return closed
