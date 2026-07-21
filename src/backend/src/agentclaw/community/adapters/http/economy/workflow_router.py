"""Workflow router for economy/governance 正常业务流程 (AuthPlugin auth).

评审场景独立 router — 把评审运营同学需要的工单视角查询/审批能力从
admin_router 抽出来(审批能力迁出 admin_router,运维归运维、审批归审批),
并作为治理「正常业务流程」面,为后续正常流程端点扩展留位:

  - GET  /workflow/tickets                 工单列表(按治理状态过滤 + 分页)
  - GET  /workflow/tickets/detail          单工单详情(ticket_id 走 query)
  - GET  /workflow/tickets/pending-notification  查工单待回复通知(notification_id)
  - POST /workflow/tickets/review          审批动作(ticket_id 走 body,waiting_review 三态流转)
  - POST /workflow/tickets:delete-cascade 精确级联删单工单 + 连带通知(best-effort,非批量,2026-07-17 从 admin 迁入)
  - GET  /workflow/audit-logs              按 worker 查全部治理审计(只读分页)

数据流转全程走领域模型(``GovernanceTicket`` / ``TicketActionOutcome``),
router 层用带 ``from_ticket()`` / ``from_outcome()`` 的 Pydantic schema 做
显式序列化,统一响应壳 ``ApiResponse``。DB 逻辑全部委托
:class:`GovernanceAdminService`,router 不直接开 ORM 会话。

路径风格:全 body/query 驱动,ticket_id 不进 path(与 admin 写操作统一)。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.economy.schemas import (
    ApiResponse,
    AuditLogItemResponse,
    ReviewTicketDetailResponse,
    ReviewTicketListResponse,
    TicketDeleteCascadeRequest,
    TicketsCloseAllRequest,
    TicketsCloseRequest,
    WorkflowReviewRequest,
    WorkflowReviewResponse,
)
from agentclaw.community.api.governance_service import (
    GovernanceAuditReadServiceProtocol,
    GovernanceWorkflowServiceProtocol,
)
from agentclaw.community.di import Injected


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workflow router — /api/economy/governance/workflow (AuthPlugin auth)
# ---------------------------------------------------------------------------

workflow_router = APIRouter(
    prefix="/api/economy/governance/workflow",
    tags=["economy-governance-workflow"],
)

_AdminSvc = GovernanceWorkflowServiceProtocol

# 评审允许的治理状态过滤值(open / scheduled = 活跃, waiting_review = 待审阅,
# closed = 已关闭, observed = 白名单观察态)。
_ALLOWED_REVIEW_STATUSES = frozenset(
    {"open", "scheduled", "waiting_review", "closed", "observed"}
)

# 投递状态核心正规状态集(pending / sent / failed / cancelled)。
# 核心四态由 notify 生命周期驱动写入;非闭集 — 不阻历史遗留/扩展值(如 none
# 列默认哨兵、first_send:sent 旧拼接格式),前端按需传任意列原始值精确查。
# delivery_status 的状态由 notify 驱动写入;各种状态查询由前端组合,后端只精确
# 匹配不做扩展/归一,保持后端逻辑干净。
_ALLOWED_DELIVERY_STATUSES = frozenset(
    {"pending", "sent", "failed", "cancelled"}
)


# ---------------------------------------------------------------------------
# Private helpers — keeps handlers short
# ---------------------------------------------------------------------------


def _raise_on_admin_error(result: object) -> None:
    """Raise HTTPException if the admin-service result contains an error.

    与 admin_router 同阶实现(~10 行重复,刻意不抽共享 helper 避免跨文件
    重构噪音;评审错误码 NOT_FOUND/INVALID_STATUS/INVALID_ACTION → 404/400/400)。
    """
    error = getattr(result, "error", None)
    if error:
        error_code = getattr(result, "error_code", "") or ""
        status_code = 404 if error_code == "NOT_FOUND" else 400
        raise HTTPException(status_code=status_code, detail=error)


def _validate_status_filter(statuses: list[str] | None) -> list[str] | None:
    """校验 ``statuses`` query 取值,返回归一化后的列表。

    语义守恒(与 service 层 ``list_review_tickets`` 契约一致):
      - None  = 缺省 → service 填默认活跃态
      - []    = 显式空过滤 → service 走空结果路径(不得在此回落默认)
      - 非空  = 任一非法值 → 400
    """
    if statuses is None:
        return None
    if len(statuses) == 0:
        return []
    invalid = [s for s in statuses if s not in _ALLOWED_REVIEW_STATUSES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid statuses {invalid!r}; allowed: "
                f"{sorted(_ALLOWED_REVIEW_STATUSES)}"
            ),
        )
    return statuses


def _validate_delivery_status_filter(delivery_statuses: list[str] | None) -> list[str] | None:
    """校验 ``delivery_status`` query 取值,返回归一化后的列表。

    语义守恒(只做 None/[] 短路,不做枚举校验):
      - None  = 缺省 → 不过滤投递态
      - []    = 显式空过滤 → 空结果路径
      - 非空  = 直通(不拦非核心值;前端可传 none 历史哨兵 / first_send:sent 旧格式
        等任意列原始值,SQL 精确匹配 IN(...))

    核心正规状态集 ``_ALLOWED_DELIVERY_STATUSES`` 仅作文档/校验参考(非闭集)。
    delivery_status 状态由 notify 驱动写入;查询组合由前端做,后端精确匹配不扩展。
    """
    if delivery_statuses is None:
        return None
    if len(delivery_statuses) == 0:
        return []
    return delivery_statuses


# ── Workflow: 工单列表 ────────────────────────────────────────────────────


@workflow_router.get(
    "/tickets",
    summary="工单列表(按治理状态过滤 + 分页)",
)
async def list_review_tickets(
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
    statuses: list[str] | None = Query(
        default=None,
        description=(
            "治理状态过滤(允许: open / scheduled / waiting_review / closed);"
            "缺省 = 全部活跃态(open+scheduled+waiting_review)"
        ),
    ),
    delivery_status: list[str] | None = Query(
        default=None,
        description=(
            "投递状态过滤(允许: pending / sent / failed / cancelled);"
            "缺省 = 不过滤"
        ),
    ),
    offset: int = Query(0, ge=0, description="分页偏移"),
    limit: int = Query(50, ge=1, le=200, description="分页上限(<=200)"),
) -> ApiResponse:
    """工单列表 — 活跃(open∪scheduled) / 待审阅(waiting_review) / 已关闭(closed)。

    只读 GET,不产生副作用、不写 audit。
    """
    del ctx  # RequestContext 仅用于走 AuthPlugin 鉴权链路
    normalized = _validate_status_filter(statuses)
    delivery_normalized = _validate_delivery_status_filter(delivery_status)
    tickets, total = await asyncio.to_thread(
        admin_svc.list_review_tickets,
        normalized,
        offset=offset,
        limit=limit,
        delivery_statuses=delivery_normalized,
    )
    status_filter = normalized if normalized is not None else [
        "open", "scheduled", "waiting_review",
    ]
    data = ReviewTicketListResponse.from_tickets(
        tickets,
        total=total,
        limit=limit,
        offset=offset,
        status_filter=status_filter,
    )
    return ApiResponse(success=True, data=data.model_dump())


@workflow_router.get(
    "/tickets:whitelist",
    summary="白单观察工单视图(OBSERVED)",
)
async def list_whitelist_tickets(
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
    offset: int = Query(0, ge=0, description="分页偏移"),
    limit: int = Query(50, ge=1, le=200, description="分页上限(<=200)"),
) -> ApiResponse:
    """当前处于 OBSERVED 观察态的工单(加白中 bot 的最新治理画像)。

    只读 GET,复用工单列表 item 结构,按 gmt_create 倒序分页。
    item = 纯工单(ReviewTicketItem),含治理画像(token_baseline/hit_dimensions/
    saving_ratio/latest_decision/dt_version 等);不并白单元数据(来源/过期
    另查 /admin/whitelist)。无 OBSERVED 工单时 items=[] total=0。
    """
    del ctx  # RequestContext 仅用于走 AuthPlugin 鉴权链路
    tickets, total = await asyncio.to_thread(
        admin_svc.list_whitelist_observed_tickets,
        offset=offset,
        limit=limit,
    )
    data = ReviewTicketListResponse.from_tickets(
        tickets,
        total=total,
        limit=limit,
        offset=offset,
        status_filter=["observed"],
    )
    return ApiResponse(success=True, data=data.model_dump())


# ── Workflow: 单工单详情 ──────────────────────────────────────────────────


@workflow_router.get(
    "/tickets/detail",
    summary="单工单详情",
)
async def get_review_ticket_detail(
    ticket_id: str = Query(..., description="工单 ID"),
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """工单全貌(基础信息 / 用户反馈 / 命中维度 / 节省率 / review_reason ...)。

    只读 GET;工单不存在 → 404。ticket_id 走 query(与 admin 写操作零 path 参数风格统一)。
    """
    del ctx  # RequestContext 仅用于走 AuthPlugin 鉴权链路
    detail = await asyncio.to_thread(
        admin_svc.build_review_ticket_detail, ticket_id,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket, in_whitelist = detail
    data = ReviewTicketDetailResponse.from_ticket(ticket, in_whitelist=in_whitelist)
    return ApiResponse(success=True, data=data.model_dump())


# ── Workflow: 待回复通知查询 ─────────────────────────────────────────────


@workflow_router.get(
    "/tickets/pending-notification",
    summary="查工单待回复通知(notification_id)",
)
async def get_pending_notification(
    ticket_id: str = Query(..., description="工单 ID"),
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """查工单当前通知的 notification_id。

    open 工单(用户未反馈)的 notification_id 在 notify_log,不在 task_record。
    前端 admin review / card-callback 推进状态时需此 ID。
    优先返回 pending/sending(待回复);无则最近一条 sent。
    """
    del ctx  # RequestContext 仅用于走 AuthPlugin 鉴权链路
    result = await asyncio.to_thread(admin_svc.get_pending_notification, ticket_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No notification found for ticket")
    return ApiResponse(success=True, data=result)


# ── Workflow: 审计查询(只读) ──────────────────────────────────────────────

_AuditReadSvc = GovernanceAuditReadServiceProtocol


@workflow_router.get(
    "/audit-logs",
    summary="按 worker 查全部治理审计(只读分页,owner/bot/复合 worker_id)",
)
async def list_audit_logs(
    ctx: RequestContext = Depends(get_request_context),
    audit_read_svc: _AuditReadSvc = Injected(_AuditReadSvc),
    worker_id: str | None = Query(
        None, description="复合 worker_id (owner_id:bot_id),优先解析",
    ),
    owner_id: str | None = Query(None, description="按 owner_id 精确筛选"),
    bot_id: str | None = Query(None, description="按 bot_id 精确筛选"),
    action: str | None = Query(
        None, description="按 action_taken 过滤(AuditAction 枚举值)",
    ),
    limit: int = Query(50, ge=1, le=200, description="分页上限 1~200"),
    offset: int = Query(0, ge=0, description="分页偏移"),
) -> ApiResponse:
    """只读分页治理审计查询。

    按 worker 维度拉取该 worker 全历史治理审计动作(加白/删白/审批/关单/
    扫描命中/通知发送/反馈),按 ``gmt_create`` 倒序分页,带 ``total``。
    ``worker_id(owner_id:bot_id)`` 优先解析,覆盖独立的 ``owner_id``/
    ``bot_id``;``action`` 可选按 ``action_taken`` 过滤。至少需一个过滤维度
    (worker/owner/bot/action),否则 400(防全表扫)。

    只读:无副作用、不写审计。item 经 ``AuditLogItemResponse`` 序列化(对齐
    ``AuditLogOrm.to_dict()``)。鉴权走 ``get_request_context``(同他端点)。
    """
    del ctx  # RequestContext 仅用于走 AuthPlugin 鉴权链路
    try:
        items, total = await asyncio.to_thread(
            audit_read_svc.list_audit_by_worker,
            worker_id=worker_id,
            owner_id=owner_id,
            bot_id=bot_id,
            action=action,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(
        success=True,
        data={
            "items": [AuditLogItemResponse(**it).model_dump(mode="json") for it in items],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    )


# ── Workflow: 审批动作 ────────────────────────────────────────────────────


@workflow_router.post(
    "/tickets/review",
    summary="审批动作(waiting_review 三态流转)",
)
async def review_ticket(
    body: WorkflowReviewRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """审批:approve_close / approve_whitelist / reject_for_reopen(§7.5.2)。

    ticket_id 走 body(与 admin 写操作统一,零 path 参数);委托
    :meth:`GovernanceAdminService.review_ticket`,不改变状态机语义。
    审计操作人严格取自鉴权上下文 ``ctx.user_id``(不允许 body 顶替)。
    """
    result = await asyncio.to_thread(
        admin_svc.review_ticket,
        ticket_id=body.ticket_id,
        action=body.action,
        admin_id=ctx.user_id,
        remark=body.remark,
    )
    _raise_on_admin_error(result)
    data = WorkflowReviewResponse.from_outcome(result)
    return ApiResponse(success=True, data=data.model_dump())


# ── Workflow: 关单(从 admin_router 迁入,工单运营归属) ─────────────────


@workflow_router.post(
    "/tickets:close",
    summary="关闭工单(单/多,body ticket_ids 循环 admin_close)",
)
async def tickets_close(
    body: TicketsCloseRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Close one or more governance tickets (admin_close 循环)。

    单条/批量统一入参:handler 循环调 ``admin_svc.admin_close``(已委托
    ``lifecycle_svc``,关工单 + cancel_pending 由 driver 编排)。禁止直调 repo。
    """
    operator = ctx.user_id

    def _close_all():
        results = []
        for ticket_id in body.ticket_ids:
            result = admin_svc.admin_close(
                ticket_id=ticket_id,
                admin_id=operator,
                reason=body.reason,
            )
            results.append(result.to_dict())
        return results

    results = await asyncio.to_thread(_close_all)
    return ApiResponse(success=True, data=results)


@workflow_router.post(
    "/tickets:close-all",
    summary="全部关单(dispatch:cancel_pending 仅未响应 / close_all_open 全量)",
)
async def tickets_close_all(
    body: TicketsCloseAllRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Close all active governance tickets (admin bulk)。

    ``only_unresponded=true`` → ``cancel_pending``(仅未响应,ADMIN_CLOSED,
    label=cancelled);否则 → ``close_all_open``(全量含已响应,ADMIN_CLOSED,
    label=closed)。两方法经状态机 Task 8 已联合编排 task_record 主体 +
    notify_log 通知 + audit。cooldown_days 走 config(无入参)。
    """
    operator = ctx.user_id
    if body.only_unresponded:
        result = await asyncio.to_thread(
            admin_svc.cancel_pending, reason=body.reason, operator=operator,
        )
    else:
        result = await asyncio.to_thread(
            admin_svc.close_all_open, reason=body.reason, operator=operator,
        )
    return ApiResponse(success=True, data=result.to_dict())


# ── Workflow: 级联删工单(2026-07-17 从 admin_router 迁入,工单运营归属) ─


@workflow_router.post(
    "/tickets:delete-cascade",
    summary="按 ticket_id 精确级联删工单 + 连带通知(best-effort,非批量)",
)
async def tickets_delete_cascade(
    body: TicketDeleteCascadeRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Precisely delete one ticket + its notify_log rows (best-effort).

    单向(ticket → notify)、单工单(防写放大)、env-scoped。dry_run=true 仅
    预览连带通知数,不删不写审计;工单不存在返回 ticket_found=False 且不写
    审计(幂等再调)。best-effort:通知清理失败不阻断工单删除,失败计数计入
    响应与审计。
    """
    data = await asyncio.to_thread(
        admin_svc.delete_ticket_cascade,
        ticket_id=body.ticket_id,
        dry_run=body.dry_run,
        reason=body.reason,
        operator=ctx.user_id,
    )
    return ApiResponse(success=True, data=data)
