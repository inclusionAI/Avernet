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

from agentclaw.community.core.economy.governance.domain.enums import (
    AdminCloseConclusion,
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
        delivery_statuses: list[str] | None = None,
    ) -> tuple[list[GovernanceTicket], int]:
        """评审工单列表:按治理状态过滤(跨 owner)、分页,返回领域模型 + 总数。

        Args:
            statuses: 治理状态白名单(open/scheduled/waiting_review/closed/observed);
                None 时默认全部活跃态(open/scheduled/waiting_review,不含 observed);
                [] 显式表示无任何状态匹配 → 返回空(repo 层空列表短路)。
                observed = 白名单观察态,评审需显式传 status=observed 才能筛出
                观察单(默认不混入活跃列表,避免污染待办视图)。
            offset: 分页偏移。
            limit: 分页上限。
            delivery_statuses: 投递状态白名单(pending/sent/failed/cancelled);
                None 时不过滤;[] 空列表短路返回空。

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
            delivery_statuses=delivery_statuses,
        )
        total = self._task_repo.count_tickets_by_statuses(
            effective, delivery_statuses=delivery_statuses,
        )
        return tickets, total

    def list_whitelist_observed_tickets(
        self, *, offset: int = 0, limit: int = 50,
    ) -> tuple[list[GovernanceTicket], int]:
        """白单观察工单视图:当前处于 OBSERVED 观察态的工单(加白中 bot 最新治理画像)。

        薄包装,语义入口 — 转调 :meth:`list_review_tickets` 显式传 ``[observed]``,
        复用既有查询/分页/counts 口径,凸出「白单观察工单视图」语义,不重写逻辑。
        OBSERVED 态归终态族(`ACTIVE_STATUSES` 不含),默认活跃列表不混入观察单,
        故白单视图必须由本入口显式筛 observed。

        Args:
            offset: 分页偏移。
            limit: 分页上限。

        Returns:
            ``(OBSERVED 工单列表, 总数)``;无观察单时 ``([], 0)``。
        """
        return self.list_review_tickets(
            [GovernanceStatus.OBSERVED.value], offset=offset, limit=limit,
        )

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

    def list_ticket_history_by_worker(
        self,
        *,
        worker_id: str | None = None,
        owner_id: str | None = None,
        bot_id: str | None = None,
        limit: int = 5,
    ) -> tuple[list[GovernanceTicket], str | None, str | None, str | None]:
        """按 worker 取最近 N 条工单历史(全状态,gmt_create 倒序),辅助关单-重开决策。

        管理员处理新开/重开工单时,借此横向看该 worker 的工单生命周期(关单原因/
        裁定结论/用户反馈/审批记录),辅助本次决策。只读、无副作用、不写审计。

        worker_id(``owner:bot``)优先解析并覆盖独立的 ``owner_id``/``bot_id``;解析后
        三者皆空抛 :class:`ValueError`(路由侧 400,防全表扫,对齐
        ``GovernanceAuditReadService.list_audit_by_worker`` 口径)。解析逻辑与
        ``audit_read_service._parse_worker_id`` 同款(刻意不跨服务复用,避免
        workflow→audit_read 依赖)。

        Args:
            worker_id: 复合标识 ``"owner_id:bot_id"``,优先解析。
            owner_id: 独立按 owner 查(被 worker_id 覆盖)。
            bot_id: 独立按 bot 查(被 worker_id 覆盖)。
            limit: 取数上限 1~50(路由层 Query 已校验)。

        Returns:
            ``(tickets, resolved_owner_id, resolved_bot_id, worker_id_echo)``。
            ``worker_id_echo``:给了 worker_id 或同时给 owner+bot 时回显
            ``"owner:bot"``;仅给单维度时为 None。

        Raises:
            ValueError: ``worker_id`` 非 ``owner:bot`` 形态,或解析后 owner/bot/worker
                三者皆空。
        """
        if worker_id is not None:
            owner_id, bot_id = self._parse_worker_id(worker_id)
        if owner_id is None and bot_id is None:
            raise ValueError(
                "at least one of worker_id / owner_id / bot_id is required"
            )
        tickets = self._task_repo.list_recent_tickets_by_worker(
            # worker_id 原值优先(repo 走 worker_id == 等值,最精确且命中 worker 索引);
            # 仅给 owner/bot 单维度时传 None,repo 走列过滤。
            worker_id=worker_id if worker_id is not None else None,
            owner_id=owner_id,
            bot_id=bot_id,
            limit=limit,
        )
        can_compose = (
            worker_id is not None
            or (owner_id is not None and bot_id is not None)
        )
        echo_worker = f"{owner_id}:{bot_id}" if can_compose else None
        return tickets, owner_id, bot_id, echo_worker

    @staticmethod
    def _parse_worker_id(worker_id: str) -> tuple[str, str]:
        """解析 ``"owner_id:bot_id"`` 为 ``(owner_id, bot_id)``。

        逻辑与 :meth:`GovernanceAuditReadService._parse_worker_id` 逐字一致
        (单冒号、两段非空、无内嵌空白)。刻意不抽公共 util(对齐 workflow_router
        ``_raise_on_admin_error`` "不抽共享 helper 避免跨文件重构噪音"原则);
        两处独立维护,如需收口再单开 PR 提到 utils。

        Raises:
            ValueError: 缺冒号 / 多于一个冒号 / 任一段为空或含空白。
        """
        parts = worker_id.split(":")
        if len(parts) != 2:
            raise ValueError(
                f"invalid worker_id {worker_id!r}: expected 'owner_id:bot_id'"
            )
        owner, bot = parts[0].strip(), parts[1].strip()
        if not owner or not bot:
            raise ValueError(
                f"invalid worker_id {worker_id!r}: owner and bot must be non-empty"
            )
        if any(ch.isspace() for ch in owner + bot):
            raise ValueError(
                f"invalid worker_id {worker_id!r}: owner and bot must not contain whitespace"
            )
        return owner, bot

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
            close_reason = CloseReason.WHITELIST_APPROVED
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

        outcome_status = self._review_outcome_status(action)
        return TicketActionOutcome(
            ticket_id=ticket_id,
            status=outcome_status,
            close_reason=close_reason,
        )

    # ── 关单方法(从 admin_service 迁入,工单运营面归属) ─────────────────


    @staticmethod
    def _review_outcome_status(action: str) -> GovernanceStatus:
        """review_ticket 返回值的状态映射(action → 工单终态)。

        与领域层 ``GovernanceTicket.review()`` 的四态分支保持一致(单一事实源
        在领域模型,此处仅镜像用于 API 响应)。approve_whitelist → OBSERVED
        (白名单观察态);approve_scheduled → SCHEDULED;其余 → CLOSED。
        """
        if action == "approve_scheduled":
            return GovernanceStatus.SCHEDULED
        if action == "approve_whitelist":
            return GovernanceStatus.OBSERVED
        return GovernanceStatus.CLOSED

    def admin_close(
        self, ticket_id: str, admin_id: str, *,
        conclusion: AdminCloseConclusion,
        close_payload: str | None = None,
    ) -> TicketActionOutcome:
        """Admin close: close ticket + set cooldown + cancel pending (§6.3).

        管理员检查后关单,使用 cooldown_days 设冷却(关单后 N 天内不重建)。

        Args:
            ticket_id: 工单 ID。
            admin_id: 操作人 ID(取自鉴权上下文,不允许 body 顶替)。
            conclusion: 管理员关单结论裁定(``AdminCloseConclusion`` 枚举)。必传,
                非法值由枚举类型本身拒绝(路由层 422)。
            close_payload: 关单明细 JSON 字符串(当前 ``{"remark": ...}``),
                由 router 从 ``CloseDetailPayload`` 序列化灌入。None = 无手写说明。

        ``conclusion`` / ``close_payload`` 透传 lifecycle driver → 领域 close()
        落盘 ``close_conclusion`` / ``close_payload`` 两列(对标用户反馈落盘)。
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
            close_conclusion=conclusion.value,
            close_payload=close_payload,
        )

        # 审计结构化记录 conclusion + remark(从 close_payload 解出),不再只记裸 reason。
        # cooldown_until 防 None:本路径恒为 now+timedelta,但守 fail-closed,避免
        # 未来 cooldown_days=0 改传 None 时 isoformat() 崩 admin_close 整条链。
        cooldown_str = cooldown_until.isoformat() if cooldown_until else "None"
        audit_msg = f"ticket_id={ticket_id}; conclusion={conclusion.value}; cooldown_until={cooldown_str}"
        if close_payload:
            audit_msg += f"; close_payload={close_payload}"

        self._audit_repo.add_audit(
            "admin-close",
            bot_id=ticket.bot_id,
            owner_id=ticket.owner_id,
            actor_id=admin_id,
            action_taken=AuditAction.ADMIN_CLOSE_ALL,
            source="admin_api",
            error_msg=audit_msg,
            dry_run=0,
        )

        return TicketActionOutcome(
            ticket_id=ticket_id,
            status=GovernanceStatus.CLOSED,
            close_reason=CloseReason.ADMIN_CLOSED,
        )

    def set_override_owner(
        self, ticket_id: str, override_owner: str | None, *, operator: str,
    ) -> TicketActionOutcome:
        """设/清工单通知收件人覆盖 (D1/D4: bot 转交/机器人 owner 代联)。

        只改 ``task_record.override_owner`` 字段,不碰状态机/owner_id/cooldown。
        ``override_owner`` 非空 → 设(后续 reminder 建通知发给此人);空串/None
        → 清(恢复发原 owner)。reminder 建通知时 ``override or owner`` 写进
        notify_log.owner_id(notify_log 零增列语义自洽)。

        Args:
            ticket_id: 工单稳定 UUID。
            override_owner: 覆盖收件人 staffId;None/空串 = 清除。
            operator: 操作人 user_id (audit actor_id)。

        Returns:
            TicketActionOutcome(工单当前 status;不存在 → NOT_FOUND)。
        """
        ticket = self._task_repo.find_by_ticket_id(ticket_id)
        if not ticket:
            return TicketActionOutcome(
                ticket_id=ticket_id, status=GovernanceStatus.OPEN,
                error="Ticket not found", error_code="NOT_FOUND",
            )

        # 空串归一为 None(清除语义);去空白
        normalized = (override_owner or "").strip() or None
        ticket.override_owner = normalized
        if not self._task_repo.save_ticket(ticket):
            return TicketActionOutcome(
                ticket_id=ticket_id, status=ticket.governance_status,
                error="Failed to persist override_owner", error_code="PERSIST_FAILED",
            )

        self._write_admin_audit(
            action_taken=AuditAction.ADMIN_OVERRIDE_OWNER,
            actor_id=operator,
            error_msg=(
                f"ticket_id={ticket_id}; override_owner={normalized or '(cleared)'}"
            ),
        )
        log.info(
            "[GovernanceAdmin] set_override_owner by %s on %s: override_owner=%s",
            operator, ticket_id, normalized,
        )
        return TicketActionOutcome(
            ticket_id=ticket_id, status=ticket.governance_status,
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
        # Driver's admin_close uses ADMIN_CLOSED (aligns notify side).批量关单
        # 统一落 BULK_CLOSED 结论(D4:每张被关工单带同一批量结论值)。
        self._lifecycle_svc.bulk_close_by_ticket_ids(
            ticket_ids, now=now,
            close_conclusion=AdminCloseConclusion.BULK_CLOSED.value,
        )

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
        # 批量关单统一落 BULK_CLOSED 结论(D4:每张被关工单带同一批量结论值)。
        tickets_closed = self._lifecycle_svc.bulk_close_open(
            close_reason=CloseReason.ADMIN_CLOSED, now=now,
            close_conclusion=AdminCloseConclusion.BULK_CLOSED.value,
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

        # 先删通知、再删工单:崩溃在两步之间时,通知不会孤立(工单仍在,重试可重入
        # 删通知→删工单)。若反过来先删工单,崩溃在删通知前会让通知永久孤立(
        # 工单没了,重试因 ticket is None 短路,清不掉残留通知)。
        notify_deleted, notify_failed = self._cascade_delete_notify(ticket_id)
        tickets_deleted = self._task_repo.delete_by_ticket_id(ticket_id)
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
