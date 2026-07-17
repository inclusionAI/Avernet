"""[编排] Governance workflow service — 工单审批(§7.5.2)。

从 GovernanceAdminService 按对外路由边界拆出(admin_router / workflow_router 各管一面):
管理面(紧急制动/批量/删除/手动投递)留 admin_service;审批面(列表/详情/动作)
归本服务。从 workflow_router 看,审批端点(list_review_tickets / get_review_ticket_detail
/ review_ticket)对应本服务三方法,管理端点对应 admin_service。

依赖边界:
  - 上行(web):workflow_router 注入 GovernanceWorkflowServiceProtocol(api re-export)。
  - 下行(repo):task_repo(list/count/find)/ audit_repo(add_audit)。
  - 横向(service):lifecycle_svc.review_ticket(状态机唯一驱动)/ whitelist_service.add
    (approve_whitelist 副作用);不反向依赖 admin_service。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.core.economy.governance.domain.base import (
    build_delivery_status_json,
)
from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    CloseReason,
    GovernanceStatus,
)
from agentclaw.community.core.economy.governance.services.admin_service import (
    BulkOperationResult,
    TicketActionOutcome,
)
from agentclaw.community.core.economy.governance.services.service_protocols import (
    GovernanceLifecycleServiceProtocol,
    GovernanceWhitelistServiceProtocol,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.domain.ticket import (
        GovernanceTicket,
    )
    from agentclaw.community.core.economy.governance.repositories.audit_repo import (
        GovernanceAuditRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
        TaskRecordRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
        NotifyLogRepository,
    )

log = get_logger(__name__)


class GovernanceWorkflowService:
    """工单运营服务 — list / detail / review / 关单 / 级联清理(§7.5.2)。

    review 三分支走 lifecycle_svc.review_ticket(状态机唯一驱动) + 审批副作用
    (approve_whitelist 加白名单)+ 审计。关单方法(admin_close /
    cancel_pending / close_all_open,从 admin_service 迁入)同走状态机 +
    audit;级联清理(delete_ticket_cascade:按 ticket_id 精确删单工单 + 连带
    notify,best-effort)亦归本服务(2026-07-17 从 admin_service 迁入,贴合
    工单运营边界)。依赖复用本服务既有注入(task_repo/audit_repo/config/
    lifecycle_svc/notify_repo),零依赖补。仅 import admin_service 的
    TicketActionOutcome / BulkOperationResult 数据类(单向,无循环)。
    """

    @inject
    def __init__(
        self,
        task_repo: TaskRecordRepository,
        audit_repo: GovernanceAuditRepository,
        config: Any,  # EconomyGovernanceConfig
        lifecycle_svc: GovernanceLifecycleServiceProtocol,
        whitelist_service: GovernanceWhitelistServiceProtocol,
        notify_repo: NotifyLogRepository,
    ) -> None:
        self._task_repo = task_repo
        self._audit_repo = audit_repo
        self._config = config
        self._lifecycle_svc = lifecycle_svc
        self._whitelist_service = whitelist_service
        self._notify_repo = notify_repo

    def list_review_tickets(
        self,
        statuses: list[str] | None,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[GovernanceTicket], int]:
        """评审工单列表:按治理状态过滤(跨 owner)、分页,返回领域模型 + 总数。

        Args:
            statuses: 治理状态白名单(open/scheduled/waiting_review/closed);
                None 时默认全部活跃态(open/scheduled/waiting_review);
                [] 显式表示无任何状态匹配 → 返回空(repo 层空列表短路)。
            offset: 分页偏移。
            limit: 分页上限。

        Returns:
            (工单领域模型列表, 满足条件的总数)。领域模型经 from_orm 灌入
            gmt_create/gmt_modified,评审列表直接用,router 层负责序列化。
        """
        effective = statuses if statuses is not None else [
            GovernanceStatus.OPEN.value,
            GovernanceStatus.SCHEDULED.value,
            GovernanceStatus.WAITING_REVIEW.value,
        ]
        tickets = self._task_repo.list_tickets_by_statuses(
            effective, offset=offset, limit=limit,
        )
        total = self._task_repo.count_tickets_by_statuses(effective)
        return tickets, total

    def get_review_ticket_detail(
        self, ticket_id: str,
    ) -> GovernanceTicket | None:
        """评审工单详情:取单个工单领域模型。

        纯取工单本体(task_record 映射),不含跨聚合派生状态(如白名单)——
        白名单等派生位由 :meth:`build_review_ticket_detail` 组装。

        Args:
            ticket_id: 工单稳定 UUID。

        Returns:
            :class:`GovernanceTicket` 或 None(不存在)。
        """
        return self._task_repo.find_by_ticket_id(ticket_id)

    def build_review_ticket_detail(
        self, ticket_id: str,
    ) -> tuple[GovernanceTicket, bool] | None:
        """组装工单详情视图:工单本体 + 是否在治理白名单中(跨聚合派生位)。

        in_whitelist 不是 ticket 领域模型的固有状态(它来自 ac_bot_whitelist
        另一聚合),故不塞进 :class:`GovernanceTicket`,而在此组装方法里算出
        随工单一并返回。复用既有 ``self._whitelist_service`` 注入,
        ``is_whitelisted`` 已含 type+env+未过期判定,只点查一次。

        Args:
            ticket_id: 工单稳定 UUID。

        Returns:
            ``(ticket, in_whitelist)``;工单不存在返回 None。
            工单缺 bot_id/owner_id 时 in_whitelist=False(防御兜底)。
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return None

        # delivery_status 补查: 当解析为 none 时,从 notify_log 反推真实状态
        # (防止工单创建后崩溃,delivery_status 未更新)。
        # delivery_status 是 snapshot 上的只读 property,直接改 _snapshot.delivery_status。
        # 兼容旧行拼接("none")与新行 JSON(notify_status=none)统一走 parse 判断。
        if ticket.delivery_status_json["notify_status"] == "none":
            notified = self._notify_repo.list_by_ticket(ticket_id, only_pending=False)
            if notified:
                latest = notified[0]  # newest first
                notify_type = latest.notify_type.value if latest.notify_type else "first_send"
                notify_status = latest.delivery_status.value if latest.delivery_status else "pending"
                ticket._snapshot.delivery_status = build_delivery_status_json(  # noqa: SLF001
                    notify_type, notify_status,
                )

        bot_id = getattr(ticket, "bot_id", None)
        owner_id = getattr(ticket, "owner_id", None)
        if not bot_id or not owner_id:
            return ticket, False
        return ticket, self._whitelist_service.is_whitelisted(bot_id, owner_id)

    def get_pending_notification(self, ticket_id: str) -> dict | None:
        """查工单待回复(sent/pending)通知,返回 notification_id + 元信息。

        前端 admin review 时需拿到 notification_id 用于 card-callback 推进状态。
        open 工单的 notification_id 不在 task_record,在 notify_log(正向查)。

        Args:
            ticket_id: 工单稳定 UUID。

        Returns:
            ``{notification_id, notify_status, notify_type, gmt_create}`` 或 None。
        """
        # 优先 pending/sending(待回复);若无,取最近一条 sent(已发未反馈)
        notifies = self._notify_repo.list_by_ticket(ticket_id, only_pending=True)
        if not notifies:
            notifies = self._notify_repo.list_by_ticket(ticket_id)
        if not notifies:
            return None
        n = notifies[0]  # 最新
        return {
            "notification_id": n.notification_id,
            "notify_status": n.delivery_status.value if n.delivery_status else None,
            "notify_type": n.notify_type.value if n.notify_type else None,
            "sent_at": n.sent_at.isoformat() if n.sent_at else None,
        }

    def review_ticket(
        self, ticket_id: str, action: str, admin_id: str, remark: str = "",
    ) -> TicketActionOutcome:
        """Admin review: waiting_review → closed (§7.5.2).

        Actions: approve_close / approve_whitelist / reject_for_reopen.
        """
        valid_actions = {
            "approve_close", "approve_scheduled", "approve_whitelist", "reject_for_reopen",
        }
        if action not in valid_actions:
            return TicketActionOutcome(
                ticket_id=ticket_id, status=GovernanceStatus.WAITING_REVIEW,
                error=f"Invalid action: {action}", error_code="INVALID_ACTION",
            )

        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if not ticket:
            return TicketActionOutcome(
                ticket_id=ticket_id, status=GovernanceStatus.WAITING_REVIEW,
                error="Ticket not found", error_code="NOT_FOUND",
            )

        if ticket.governance_status != GovernanceStatus.WAITING_REVIEW:
            return TicketActionOutcome(
                ticket_id=ticket_id,
                status=GovernanceStatus(ticket.governance_status),
                error=f"Ticket not in waiting_review (status={ticket.governance_status})",
                error_code="INVALID_STATUS",
            )

        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        close_reason: str
        cooldown_until: datetime | None = None

        if action == "approve_close":
            review_reason = ticket.review_reason or "unknown"
            close_reason = f"{review_reason}_approved"
            cooldown_until = now + timedelta(days=cooldown_days)
        elif action == "approve_scheduled":
            # 同意排期 → SCHEDULED(不关单),close_reason 作 review 标记;
            # 不设 cooldown,保留 ticket.repair_deadline(need_time 反馈时记录)
            close_reason = "schedule_approved"
            cooldown_until = None
        elif action == "approve_whitelist":
            close_reason = "whitelist_approved"
            cooldown_until = None
            try:
                self._whitelist_service.add(
                    bot_id=ticket.bot_id,
                    owner_id=ticket.owner_id,
                    created_by=admin_id,
                    whitelist_type="governance",
                    source="admin_review",
                )
            except Exception:
                log.exception(
                    "[GovernanceWorkflow] Failed to add whitelist for bot_id=%s",
                    ticket.bot_id,
                )
        elif action == "reject_for_reopen":
            close_reason = "review_rejected"
            cooldown_until = None

        # Advance via driver service (sole driver). Driver orchestrates the
        # WAITING_REVIEW → CLOSED (three-branch) transition + one-way
        # cancel-pending side effect. ``close_reason`` resolved above per
        # action; driver's model.review() also sets it but we pass the
        # already-computed value so approve_close carries the
        # `{review_reason}_approved` form exactly.
        self._lifecycle_svc.review_ticket(
            ticket_id,
            review_decision=action,
            reviewed_by=admin_id,
            reviewed_at=now,
            close_reason=close_reason,
            cooldown_until=cooldown_until,
            review_remark=remark,
        )

        audit_action_map = {
            "approve_close": AuditAction.REVIEW_APPROVE_CLOSE,
            "approve_scheduled": AuditAction.REVIEW_APPROVE_SCHEDULED,
            "approve_whitelist": AuditAction.REVIEW_APPROVE_WHITELIST,
            "reject_for_reopen": AuditAction.REVIEW_REJECT_FOR_REOPEN,
        }
        self._audit_repo.add_audit(
            "admin-review",
            bot_id=ticket.bot_id,
            owner_id=ticket.owner_id,
            actor_id=admin_id,
            action_taken=audit_action_map.get(action, action),
            source="admin_api",
            error_msg=f"ticket_id={ticket_id}; action={action}; remark={remark}",
            dry_run=0,
        )

        outcome_status = (
            GovernanceStatus.SCHEDULED if action == "approve_scheduled"
            else GovernanceStatus.CLOSED
        )
        return TicketActionOutcome(
            ticket_id=ticket_id,
            status=outcome_status,
            close_reason=close_reason,
        )

    # ── 关单方法(从 admin_service 迁入,工单运营面归属) ─────────────────


    def admin_close(
        self, ticket_id: str, admin_id: str, reason: str = "",
    ) -> TicketActionOutcome:
        """Admin close: close ticket + set cooldown + cancel pending (§6.3).

        管理员检查后关单,使用 cooldown_days 设冷却(关单后 N 天内不重建)。
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if not ticket:
            return TicketActionOutcome(
                ticket_id=ticket_id, status=GovernanceStatus.OPEN,
                error="Ticket not found", error_code="NOT_FOUND",
            )

        if ticket.governance_status == GovernanceStatus.CLOSED:
            return TicketActionOutcome(
                ticket_id=ticket_id,
                status=GovernanceStatus.CLOSED,
                close_reason=ticket.close_reason,
            )

        now = datetime.now()
        cooldown_days = self._config.cooldown_days
        cooldown_until = now + timedelta(days=cooldown_days)

        # Advance via driver service (sole driver). Driver orchestrates the
        # CLOSE + cooldown + cancel-pending.
        self._lifecycle_svc.admin_close(
            ticket_id, now=now, cooldown_until=cooldown_until,
        )

        self._audit_repo.add_audit(
            "admin-close",
            bot_id=ticket.bot_id,
            owner_id=ticket.owner_id,
            actor_id=admin_id,
            action_taken=AuditAction.ADMIN_CLOSE_ALL,
            source="admin_api",
            error_msg=f"ticket_id={ticket_id}; reason={reason}; cooldown_until={cooldown_until.isoformat()}",
            dry_run=0,
        )

        return TicketActionOutcome(
            ticket_id=ticket_id,
            status=GovernanceStatus.CLOSED,
            close_reason=CloseReason.ADMIN_CLOSED,
        )

    def cancel_pending(self, reason: str, operator: str) -> BulkOperationResult:
        """Cancel ALL pending notifications (admin close) + close the
        matching ``task_record`` subjects (Task 8 口径对齐).

        通知侧 cancel scope = open/muted 且 response IS NULL。工单侧按被关
        通知的 ``ticket_id`` 集合关 —— **不可裸用全量** :meth:`bulk_close_open`
        (会多关已反馈的 scheduled 单)。逐条走 :meth:`admin_close` 链路
        激活领域模型守卫、幂等。

        Returns ``BulkOperationResult(affected=N, label="closed")``。
        """
        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        # Step 1: pre-collect the ticket_id set scoped to the same filter as
        # the notify bulk-cancel (only_unresponded=True), before the cancel
        # mutates rows. 无 None(record_process 创建处恒非空,且查询已剔 None)。
        ticket_ids = self._notify_repo.list_ticket_ids_open_muted(
            only_unresponded=True,
        )

        # Step 2: notify-side bulk cancel (behavior unchanged) — mirrors
        # notify_status/governance_status/close_reason/closed_at/cooldown_until.
        cancelled = self._notify_repo.bulk_close_open_muted(
            close_reason=CloseReason.ADMIN_CLOSED,
            closed_at=now,
            cooldown_until=now + timedelta(days=cooldown_days),
            only_unresponded=True,
        )

        # Step 3: ticket-side close — per-ticket guard-activated, idempotent.
        # Driver's admin_close uses ADMIN_CLOSED (aligns notify side).
        self._lifecycle_svc.bulk_close_by_ticket_ids(ticket_ids, now=now)

        self._write_admin_audit(
            action_taken=AuditAction.ADMIN_CANCEL_PENDING,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceAdmin] cancel_pending by %s: cancelled=%d, tickets_closed_by=%d",
            operator, cancelled, len(ticket_ids),
        )
        return BulkOperationResult(affected=cancelled, label="closed")

    def close_all_open(self, reason: str, operator: str) -> BulkOperationResult:
        """Close ALL open/muted records, including already-responded ones,
        + close all open/scheduled ``task_record`` subjects (Task 8 口径对齐).

        Unlike :meth:`cancel_pending` which only touches ``response IS NULL``
        records, this closes **every** open/muted notification regardless of
        whether the user has already responded (e.g. ``need_time`` → muted).

        工单侧用全量 :meth:`bulk_close_open`(WHERE status IN (open,scheduled))
        ——与通知侧 ``governance_status IN (open,muted)`` 口径天然对齐(全量
        关,不区分反馈)。Existing notify ``response`` / ``response_source`` /
        ``mute_until`` preserved.

        Returns ``BulkOperationResult(affected=N, label="closed")``。
        """
        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        # Step 1: notify-side bulk close (behavior unchanged).
        closed = self._notify_repo.bulk_close_open_muted(
            close_reason=CloseReason.ADMIN_CLOSED,
            closed_at=now,
            cooldown_until=now + timedelta(days=cooldown_days),
            only_unresponded=False,
        )

        # Step 2: ticket-side full close (ADMIN_CLOSED). bulk_close_open's
        # WHERE status IN (open,scheduled) + active_worker IS NOT NULL
        # predicate is the state-legality guard (per-spec bulk exemption).
        tickets_closed = self._lifecycle_svc.bulk_close_open(
            close_reason=CloseReason.ADMIN_CLOSED, now=now,
        )

        self._write_admin_audit(
            action_taken=AuditAction.ADMIN_CLOSE_ALL,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceAdmin] close_all_open by %s: notify_closed=%d, tickets_closed=%d",
            operator, closed, tickets_closed,
        )
        return BulkOperationResult(affected=closed, label="closed")

    # -- Ticket cascade delete (admin) — precise single-ticket purge --------
    # Spec: 2026-07-17-records-delete-cascade-notify. Delete one ticket
    # (task_record_daily) + its belonging notify_log rows (best-effort),
    # keyed by ticket_id. Explicit dedicated interface — NOT an implicit
    # cascade inside records:delete, to keep precise semantics + avoid
    # write amplification (no batch).
    # 2026-07-17: 从 admin_service 迁入本服务(工单运营边界)。

    def delete_ticket_cascade(
        self, *, ticket_id: str, dry_run: bool, reason: str, operator: str,
    ) -> dict:
        """Precisely delete one ticket + its notify_log rows (best-effort).

        Single-direction cascade (ticket → its notify), keyed by ``ticket_id``.
        Best-effort: notify cleanup failure does NOT block ticket deletion;
        the failure count is recorded in the audit + response. Dry-run previews
        the linked notify count without deleting. Ticket-not-found returns
        ``ticket_found=False`` and writes no audit (idempotent re-call).

        Args:
            ticket_id: 工单稳定 UUID (env-scoped lookup).
            dry_run: True = preview only (no delete, no audit).
            reason: 写审计的删除原因。
            operator: 操作人 user_id (actor_id)。

        Returns:
            dict with keys: ticket_id, ticket_found, dry_run,
            tickets_deleted, notify_deleted, notify_delete_failed.
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if ticket is None:
            return self._cascade_result(
                ticket_id=ticket_id, ticket_found=False, dry_run=dry_run,
                tickets_deleted=0, notify_deleted=0, notify_delete_failed=0,
            )

        notify_preview = self._notify_repo.count_by_ticket_id(ticket_id)
        if dry_run:
            return self._cascade_result(
                ticket_id=ticket_id, ticket_found=True, dry_run=True,
                tickets_deleted=0, notify_deleted=notify_preview,
                notify_delete_failed=0,
            )

        tickets_deleted = self._task_repo.delete_by_ticket_id(ticket_id)
        notify_deleted, notify_failed = self._cascade_delete_notify(ticket_id)
        self._audit_cascade(
            ticket=ticket, operator=operator, reason=reason,
            tickets_deleted=tickets_deleted, notify_deleted=notify_deleted,
            notify_delete_failed=notify_failed,
        )
        return self._cascade_result(
            ticket_id=ticket_id, ticket_found=True, dry_run=False,
            tickets_deleted=tickets_deleted, notify_deleted=notify_deleted,
            notify_delete_failed=notify_failed,
        )

    def _cascade_delete_notify(self, ticket_id: str) -> tuple[int, int]:
        """Best-effort bulk-delete of notify_log rows for a ticket.

        Returns (deleted_count, failed_count). A SQL exception on the bulk
        delete is caught: nothing was deleted (single atomic statement), so
        ``failed_count`` reflects the preview count that could not be cleared.
        Never raises — caller (delete_ticket_cascade) records the failure.

        The recovery ``count_by_ticket_id`` is itself guarded: if the DB is
        hard-down (delete failed AND re-count fails), returns the sentinel
        ``-1`` so the caller still gets a non-raising response; ``-1`` signals
        "unknown residual — operator must query manually" via the audit.
        """
        try:
            deleted = self._notify_repo.delete_by_ticket_id(ticket_id)
            return deleted, 0
        except Exception:
            log.exception(
                "[GovernanceAdmin] notify cleanup failed for ticket_id=%s",
                ticket_id,
            )
            try:
                failed = self._notify_repo.count_by_ticket_id(ticket_id)
            except Exception:
                log.exception(
                    "[GovernanceAdmin] notify re-count also failed "
                    "for ticket_id=%s; reporting -1 (unknown residual)",
                    ticket_id,
                )
                failed = -1
            return 0, failed

    def _audit_cascade(
        self, *, ticket: Any, operator: str, reason: str,
        tickets_deleted: int, notify_deleted: int, notify_delete_failed: int,
    ) -> None:
        """Best-effort audit write for ticket-cascade purge."""
        run_id = f"cascade-{uuid.uuid4().hex[:8]}"
        error_msg = (
            f"reason={reason} "
            f"ticket_id={ticket.ticket_id} "
            f"worker_id={ticket.worker_id} "
            f"bot_id={ticket.bot_id} "
            f"owner_id={ticket.owner_id} "
            f"tickets_deleted={tickets_deleted} "
            f"notify_deleted={notify_deleted} "
            f"notify_delete_failed={notify_delete_failed}"
        )
        try:
            self._audit_repo.add_audit(
                run_id,
                bot_id=ticket.bot_id,
                owner_id=ticket.owner_id,
                actor_id=operator,
                action_taken=AuditAction.TICKET_CASCADE_PURGED,
                source="admin_api",
                error_msg=error_msg,
                dry_run=0,
            )
        except Exception:
            log.exception(
                "[GovernanceAdmin] audit write failed for %s",
                AuditAction.TICKET_CASCADE_PURGED,
            )

    @staticmethod
    def _cascade_result(
        *, ticket_id: str, ticket_found: bool, dry_run: bool,
        tickets_deleted: int, notify_deleted: int, notify_delete_failed: int,
    ) -> dict:
        """Build the uniform cascade response dict."""
        return {
            "ticket_id": ticket_id,
            "ticket_found": ticket_found,
            "dry_run": dry_run,
            "tickets_deleted": tickets_deleted,
            "notify_deleted": notify_deleted,
            "notify_delete_failed": notify_delete_failed,
        }

    # ── 关单:私有 audit helper(随关单方法从 admin_service 迁入) ──────


    def _write_admin_audit(
        self,
        *,
        action_taken: str,
        actor_id: str | None = None,
        error_msg: str = "",
    ) -> None:
        """Best-effort audit write for admin operations.

        Delegates to :meth:`GovernanceAuditRepository.add_audit` with an
        admin-scoped ``run_id`` and ``source``.
        """
        try:
            self._audit_repo.add_audit(
                "admin",
                action_taken=action_taken,
                actor_id=actor_id,
                error_msg=error_msg,
                source="admin_api",
                dry_run=0,
            )
        except Exception:
            log.exception("[GovernanceAdmin] Failed to write audit for %s", action_taken)
