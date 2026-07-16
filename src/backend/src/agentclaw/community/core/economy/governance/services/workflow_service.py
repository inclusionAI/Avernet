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

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from injector import inject

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
    """工单运营服务 — list / detail / review / 关单(§7.5.2)。

    review 三分支走 lifecycle_svc.review_ticket(状态机唯一驱动) + 审批副作用
    (approve_whitelist 加白名单)+ 审计。关单方法(emergency_close /
    cancel_pending / close_all_open,从 admin_service 迁入)同走状态机 +
    audit;依赖复用本服务既有注入(task_repo/audit_repo/config/lifecycle_svc
    /notify_repo),零依赖补。仅 import admin_service 的
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


    def emergency_close(
        self, ticket_id: str, admin_id: str, reason: str = "",
    ) -> TicketActionOutcome:
        """Immediate ticket close without cooldown (§6.3)."""
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

        # Advance via driver service (sole driver). Driver orchestrates the
        # CLOSE + cancel-pending. The audit row (with reason + actor_id=
        # admin_id) is owned by this service below — the driver does not
        # duplicate it, matching pause_ticket / review_ticket siblings.
        self._lifecycle_svc.emergency_close(
            ticket_id, now=now,
        )

        self._audit_repo.add_audit(
            "admin-emergency-close",
            bot_id=ticket.bot_id,
            owner_id=ticket.owner_id,
            actor_id=admin_id,
            action_taken=AuditAction.ADMIN_CLOSE_ALL,
            source="admin_api",
            error_msg=f"ticket_id={ticket_id}; reason={reason}",
            dry_run=0,
        )

        return TicketActionOutcome(
            ticket_id=ticket_id,
            status=GovernanceStatus.CLOSED,
            close_reason=CloseReason.EMERGENCY_CLOSED,
        )

    def cancel_pending(self, reason: str, operator: str) -> BulkOperationResult:
        """Cancel ALL pending notifications (emergency close) + close the
        matching ``task_record`` subjects (Task 8 口径对齐).

        通知侧 cancel scope = open/muted 且 response IS NULL。工单侧按被关
        通知的 ``ticket_id`` 集合关 —— **不可裸用全量** :meth:`bulk_close_open`
        (会多关已反馈的 scheduled 单)。逐条走 :meth:`emergency_close` 链路
        激活领域模型守卫、幂等。

        Returns ``BulkOperationResult(affected=N, label="cancelled")``。
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
            close_reason=CloseReason.EMERGENCY_CLOSED,
            closed_at=now,
            cooldown_until=now + timedelta(days=cooldown_days),
            only_unresponded=True,
        )

        # Step 3: ticket-side close — per-ticket guard-activated, idempotent.
        # Driver's emergency_close uses EMERGENCY_CLOSED (aligns notify side).
        self._lifecycle_svc.bulk_close_by_ticket_ids(ticket_ids, now=now)

        self._write_emergency_audit(
            action_taken=AuditAction.ADMIN_CANCEL_PENDING,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceEmergency] cancel_pending by %s: cancelled=%d, tickets_closed_by=%d",
            operator, cancelled, len(ticket_ids),
        )
        return BulkOperationResult(affected=cancelled, label="cancelled")

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

        self._write_emergency_audit(
            action_taken=AuditAction.ADMIN_CLOSE_ALL,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}",
        )
        log.info(
            "[GovernanceAdmin] close_all_open by %s: notify_closed=%d, tickets_closed=%d",
            operator, closed, tickets_closed,
        )
        return BulkOperationResult(affected=closed, label="closed")

    # ── 关单:私有 audit helper(随关单方法从 admin_service 迁入) ──────


    def _write_emergency_audit(
        self,
        *,
        action_taken: str,
        actor_id: str | None = None,
        error_msg: str = "",
    ) -> None:
        """Best-effort audit write for emergency operations.

        Delegates to :meth:`GovernanceAuditRepository.add_audit` with the
        emergency-specific ``run_id`` and ``source``.
        """
        try:
            self._audit_repo.add_audit(
                "emergency",
                action_taken=action_taken,
                actor_id=actor_id,
                error_msg=error_msg,
                source="admin_api",
                dry_run=0,
            )
        except Exception:
            log.exception("[GovernanceEmergency] Failed to write audit for %s", action_taken)
