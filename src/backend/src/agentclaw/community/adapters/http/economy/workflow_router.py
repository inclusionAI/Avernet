"""Workflow router for economy/governance 正常业务流程 (AuthPlugin auth).

评审场景独立 router — 把评审运营同学需要的工单视角查询/审批能力从
admin_router 抽出来(审批能力迁出 admin_router,运维归运维、审批归审批),
并作为治理「正常业务流程」面,为后续正常流程端点扩展留位:

  - GET  /workflow/tickets                 工单列表(按治理状态过滤 + 分页)
  - GET  /workflow/tickets/detail          单工单详情(ticket_id 走 query)
  - POST /workflow/tickets/review          审批动作(ticket_id 走 body,waiting_review 三态流转)

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
    ReviewTicketDetailResponse,
    ReviewTicketListResponse,
    WorkflowReviewRequest,
    WorkflowReviewResponse,
)
from agentclaw.community.api.governance_service import (
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
# closed = 已关闭)。
_ALLOWED_REVIEW_STATUSES = frozenset(
    {"open", "scheduled", "waiting_review", "closed"}
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
    """校验 ``statuses`` query 取值,返回归一化后的列表(空 list → None)。

    允许 None(缺省=全部活跃态);任一非法值 → 400。空 list 视为缺省,
    由 service 层填默认活跃态。
    """
    if statuses is None:
        return None
    if len(statuses) == 0:
        return None
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
    offset: int = Query(0, ge=0, description="分页偏移"),
    limit: int = Query(50, ge=1, le=200, description="分页上限(<=200)"),
) -> ApiResponse:
    """工单列表 — 活跃(open∪scheduled) / 待审阅(waiting_review) / 已关闭(closed)。

    只读 GET,不产生副作用、不写 audit。
    """
    del ctx  # RequestContext 仅用于走 AuthPlugin 鉴权链路
    normalized = _validate_status_filter(statuses)
    tickets, total = await asyncio.to_thread(
        admin_svc.list_review_tickets,
        normalized,
        offset=offset,
        limit=limit,
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
    ticket = await asyncio.to_thread(admin_svc.get_review_ticket_detail, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    data = ReviewTicketDetailResponse.from_ticket(ticket)
    return ApiResponse(success=True, data=data.model_dump())


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
