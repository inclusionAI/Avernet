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
    GovernanceStatus,
)
from agentclaw.community.core.economy.governance.services.admin_service import (
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

log = get_logger(__name__)


class GovernanceWorkflowService:
    """工单审批服务 — list / detail / review(§7.5.2)。

    review 三分支走 lifecycle_svc.review_ticket(状态机唯一驱动) + 审批副作用
    (approve_whitelist 加白名单)+ 审计。零反向依赖 admin_service。
    """

    @inject
    def __init__(
        self,
        task_repo: TaskRecordRepository,
        audit_repo: GovernanceAuditRepository,
        config: Any,  # EconomyGovernanceConfig
        lifecycle_svc: GovernanceLifecycleServiceProtocol,
        whitelist_service: GovernanceWhitelistServiceProtocol,
    ) -> None:
        self._task_repo = task_repo
        self._audit_repo = audit_repo
        self._config = config
        self._lifecycle_svc = lifecycle_svc
        self._whitelist_service = whitelist_service

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
        """评审工单详情:取单个工单领域模型,供详情面板展示。

        Args:
            ticket_id: 工单稳定 UUID。

        Returns:
            :class:`GovernanceTicket` 或 None(不存在)。
        """
        return self._task_repo.find_by_ticket_id(ticket_id)

    def review_ticket(
        self, ticket_id: str, action: str, admin_id: str, remark: str = "",
    ) -> TicketActionOutcome:
        """Admin review: waiting_review → closed (§7.5.2).

        Actions: approve_close / approve_whitelist / reject_for_reopen.
        """
        valid_actions = {"approve_close", "approve_whitelist", "reject_for_reopen"}
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

        return TicketActionOutcome(
            ticket_id=ticket_id,
            status=GovernanceStatus.CLOSED,
            close_reason=close_reason,
        )
