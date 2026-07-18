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

from dataclasses import replace
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
            owner_name=ticket.owner_name,
            token_baseline=snapshot.token_baseline,
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

    def open_observed_ticket(self, *, ticket: GovernanceTicket) -> str:
        """New ticket → OBSERVED(白名单观察):建观察单瘦路径。

        白名单 bot 在 offline-batch 命中、且无活跃单也无现存观察单时,用当前
        record 快照**新建**一条 OBSERVED 工单承载持续刷新的治理画像。与
        :meth:`open_ticket` 关键差异:状态 OPEN→OBSERVED(经守卫);不设
        close_reason(非关单转态);**assignee 直传 None 不 fallback** worker_id
        (观察不占治理人力);**不建 notify_log、不设 delivery_status**(观察单
        不发通知,"白名单不发通知"不变式核心)。

        审计归属:同 ``open_ticket``,ENQUEUED/观察审计由调用方持有,不重复写。

        Args:
            ticket: ``GovernanceTicket.create(...)`` 构造的 OPEN 模型。

        Returns:
            持久化的 ``ticket_id``。
        """
        # 复用领域单一入口 enter_observed(状态机动作:转 OBSERVED+释放 assignee+
        # 清 remind_at+不设 closed_at)。建单非关单,不传 close_reason(None)。
        ticket.enter_observed()
        snapshot = ticket.snapshot
        # 与 open_ticket 同源字段映射,差异:assignee 不 fallback、状态=observed。
        return self._task_repo.add_ticket(
            ticket_id=ticket.ticket_id or "",
            worker_id=ticket.worker_id,
            assignee=ticket.assignee,  # None — 观察不占人力,不 fallback worker_id
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
            owner_name=ticket.owner_name,
            token_baseline=snapshot.token_baseline,
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

        Display-field passthroughs (overwrite guard — incoming non-None
        overwrites existing, incoming None keeps existing, so a batch that
        omits them never erases a previously-known value):
          - ``owner_name`` — identity display name; set on the model.
          - ``token_baseline`` — a MutableSnapshot field, but intentionally
            NOT routed through the model's ``refresh_snapshot``/``replace()``
            (which would erase existing on None); set on the snapshot directly.

        Returns True if the ticket was found and updated.
        """
        # Pop non-snapshot passthroughs before delegating to the model's
        # snapshot-replace (which rejects keys absent from MutableSnapshot).
        bot_name = snapshot_fields.pop("bot_name", None)
        owner_name = snapshot_fields.pop("owner_name", None)
        token_baseline = snapshot_fields.pop("token_baseline", None)
        remind_at = snapshot_fields.pop("remind_at", "")
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        ticket.refresh_snapshot(**snapshot_fields)
        if bot_name is not None:
            ticket.bot_name = bot_name
        if owner_name is not None:
            ticket.owner_name = owner_name
        if token_baseline is not None:
            ticket._snapshot = replace(ticket._snapshot, token_baseline=token_baseline)
        if remind_at != "":  # type: ignore[comparison-overlap]
            ticket.remind_at = remind_at  # type: ignore[assignment]
        return self._task_repo._save_ticket_with_snapshot(ticket)  # noqa: SLF001 — primitive

    def observe_for_whitelist(
        self, ticket_id: str, *, close_reason: str, now: datetime,
    ) -> bool:
        """加白→转 OBSERVED 单条语义方法(四条加白入口的统一收口)。

        把活跃单转 OBSERVED(持续观察画像,不发通知)+ 释放 active_worker +
        不设 closed_at + 取消 pending 通知(best-effort)。方案 A 链路:find →
        ``ticket.enter_observed(close_reason)``(守卫激活)→ ``save_ticket`` →
        取消通知。非法转移被守卫抛出,驱动服务捕获转审计 + False。

        四条加白入口经此(scan 兜底传 SCAN_WHITELISTED;批量加白经
        :meth:`bulk_observe_by_ticket_ids` 传 WHITELIST_APPROVED;审批加白走
        review 的 enter_observed 不经此;off-batch 建单走 open_observed_ticket)。

        now 仍接受(调用方签名稳定),转 OBSERVED 不使用它(OBSERVED 不设 closed_at)。

        Returns True if the ticket was found and observed, False if not found.
        """
        del now  # OBSERVED 不设 closed_at(与 enter_observed 一致);签名保留兼容现有调用方
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        try:
            ticket.enter_observed(close_reason=close_reason)
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "observe_for_whitelist", exc)
            return False
        if not self._task_repo.save_ticket(ticket):
            return False
        self._cancel_pending(ticket_id)
        return True

    def close_observed_for_removal(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """删白收尾:OBSERVED → CLOSED(whitelist_approved) 终态 + cancel pending。

        管理员删除白名单条目时,把该 worker 的现存观察单收尾为 CLOSED(归档为
        终态,不再被 offline-batch 刷新)。best-effort:无观察单则跳过(返 False)。
        不设 cooldown(删白后等 off-batch 正常 Step6 重建新单,非删白即复活)。

        方案 A 链路:find → ``ticket.close()``(OBSERVED→CLOSED 合法,守卫激活)
        → ``save_ticket`` → 取消待通知(观察单本无 pending,best-effort 幂等 no-op)。

        审计归属:同 ``admin_close`` 约定,删白审计(WHITELIST_REMOVED)由调用方
        (whitelist_service)持有,驱动不重复写。

        Returns True if the ticket was found and closed, False if not found
        (或已非 OBSERVED — 幂等 no-op)。
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        if ticket.governance_status != GovernanceStatus.OBSERVED:
            # 非观察态(已 CLOSED/活跃)→ 幂等 no-op,不强行转
            return False
        try:
            ticket.close(
                close_reason=CloseReason.WHITELIST_APPROVED, closed_at=now,
            )
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "close_observed_for_removal", exc)
            return False
        if not self._task_repo.save_ticket(ticket):
            return False
        self._cancel_pending(ticket_id)  # best-effort:观察单本无 pending,幂等
        return True

    def close_for_stale_replace(
        self, ticket_id: str, *, now: datetime,
    ) -> bool:
        """未回复换新 → CLOSED(stale_replaced) + cancel pending。

        方案 A 链路同 observe_for_whitelist:find → ticket.close() → save →
        cancel pending。不设 cooldown_until(stale_replace 去抖靠 gmt_create 节奏,
        与 cooldown 体系隔离)。非法转移被守卫捕获 → audit + False。

        Returns True if found+closed, False if not found。
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return False
        try:
            ticket.close(
                close_reason=CloseReason.STALE_REPLACED, closed_at=now,
            )
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "close_for_stale_replace", exc)
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
        """WAITING_REVIEW → CLOSED/OBSERVED (四态分支) + cancel pending。

        方案 A 链路:find → ``ticket.review(...)``(守卫激活,四态分支,
        清 active_worker + remind_at)→ ``save_ticket`` → 取消通知(pending 按
        ticket_id 取消,不依赖后置态)。approve_whitelist 转 OBSERVED(白名单
        观察态),其余 approve_close/reject_for_reopen 转 CLOSED。

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

    def admin_close(
        self, ticket_id: str, *, now: datetime,
        cooldown_until: datetime | None = None,
    ) -> bool:
        """Any non-CLOSED → CLOSED(admin_closed) + cancel pending.

        方案 A 链路:find → 幂等检查(已 CLOSED 返 False)→
        ``ticket.close(ADMIN_CLOSED, cooldown_until=...)``(守卫激活)→
        ``save_ticket`` → 取消通知。审计由调用方(admin_service)拥有(携带 reason +
        actor_id=admin_id)—— 与 pause_ticket / review_ticket 等
        sibling 方法一致,driver 不重复写审计。

        ``cooldown_until`` 透传到领域关单,与 ``review_ticket`` 的 approve_close
        分支口径一致(关单后 N 天内不重建)。默认 None = 不设冷却(向后兼容)。
        已 CLOSED 工单短路,never 到 close,cooldown 不被覆盖(幂等保持)。

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
                close_reason=CloseReason.ADMIN_CLOSED, closed_at=now,
                cooldown_until=cooldown_until,
            )
        except IllegalTicketTransitionError as exc:
            self._audit_illegal(ticket_id, "admin_close", exc)
            return False
        if not self._task_repo.save_ticket(ticket):
            return False
        self._cancel_pending(ticket_id)
        return True

    def bulk_close_open(self, *, close_reason: str, now: datetime) -> int:
        """Bulk admin-close all open/scheduled tickets — joint orchestration:
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
        """Per-ticket admin-close by ``ticket_id`` set — Task 8 用:
        cancel_pending / bulk_whitelist 取消通知投递后,按被关通知的
        ``ticket_id`` 集合关对应 ``task_record`` 主体,口径对齐通知侧。

        逐条走 :meth:`admin_close` 链路(find→守卫激活→save→
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
            if self.admin_close(ticket_id, now=now):
                closed += 1
        return closed

    def bulk_observe_by_ticket_ids(
        self,
        ticket_ids: list[str],
        *,
        now: datetime,
        close_reason: str = CloseReason.WHITELIST_APPROVED,
    ) -> int:
        """Per-ticket observe (→OBSERVED) by ``ticket_id`` set — 批量加白收口。

        批量加白(whitelist_service.bulk_whitelist)取消通知投递后,把对应工单转
        OBSERVED(加白语义),而非 admin_close 的 CLOSED(运维关单语义)。逐条走
        :meth:`observe_for_whitelist` 守卫激活,幂等:已 OBSERVED/CLOSED/not-found/
        非法态均返 False 不计(对齐 bulk_close_by_ticket_ids 范式)。

        close_reason 默认 WHITELIST_APPROVED(批量加白 admin 主动,语义同审批加白);
        scan 兜底等其它加白入口走单条 observe_for_whitelist 传 SCAN_WHITELISTED。

        Args:
            ticket_ids: 待转观察的 ticket_id 集合(已剔 None)。
            now: 时间戳(observe_for_whitelist 内 del now,OBSERVED 不设 closed_at;
                保留签名兼容)。
            close_reason: 观察来源,默认 WHITELIST_APPROVED。

        Returns:
            实际转 OBSERVED 的工单数(不含幂等跳过的)。
        """
        del now  # observe_for_whitelist 内已 del;签名保留兼容现有调用方
        observed = 0
        for ticket_id in ticket_ids:
            if self.observe_for_whitelist(
                ticket_id, close_reason=close_reason, now=datetime.now(),
            ):
                observed += 1
        return observed
