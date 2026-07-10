"""Review router for economy/governance review endpoints (AuthPlugin auth).

评审场景独立 router — 把评审运营同学需要的工单视角查询/审批能力从
admin_router 抽出来(审批能力迁出 admin_router,运维归运维、审批归审批):

  - GET  /review/tickets                 评审工单列表(按治理状态过滤 + 分页)
  - GET  /review/tickets/{ticket_id}     单工单评审详情(评审全貌)
  - POST /review/tickets/{ticket_id}/review  审批动作(waiting_review 三态流转)

数据流转全程走领域模型(``GovernanceTicket`` / ``TicketActionOutcome``),
router 层用带 ``from_ticket()`` / ``from_outcome()`` 的 Pydantic schema 做
显式序列化,统一响应壳 ``ApiResponse``。DB 逻辑全部委托
:class:`GovernanceAdminService`,router 不直接开 ORM 会话。
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
    AdminReviewRequest,
    AdminReviewResponse,
    ApiResponse,
    ReviewTicketDetailResponse,
    ReviewTicketListResponse,
)
from agentclaw.community.api.governance_service import (
    GovernanceAdminServiceProtocol,
)
from agentclaw.community.di import Injected


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Review router — /api/economy/governance/review (AuthPlugin auth)
# ---------------------------------------------------------------------------

review_router = APIRouter(
    prefix="/api/economy/governance/review",
    tags=["economy-governance-review"],
)

_AdminSvc = GovernanceAdminServiceProtocol

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


# ── Review: 工单列表 ──────────────────────────────────────────────────────


@review_router.get(
    "/tickets",
    summary="评审工单列表(按治理状态过滤 + 分页)",
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
    """评审列表 — 活跃(open∪scheduled) / 待审阅(waiting_review) / 已关闭(closed)。

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


# ── Review: 单工单详情 ─────────────────────────────────────────────────────


@review_router.get(
    "/tickets/{ticket_id}",
    summary="单工单评审详情",
)
async def get_review_ticket_detail(
    ticket_id: str,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """评审工单全貌(基础信息 / 用户反馈 / 命中维度 / 节省率 / review_reason ...)。

    只读 GET;工单不存在 → 404。
    """
    del ctx  # RequestContext 仅用于走 AuthPlugin 鉴权链路
    ticket = await asyncio.to_thread(admin_svc.get_review_ticket_detail, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    data = ReviewTicketDetailResponse.from_ticket(ticket)
    return ApiResponse(success=True, data=data.model_dump())


# ── Review: 审批动作 ──────────────────────────────────────────────────────


@review_router.post(
    "/tickets/{ticket_id}/review",
    summary="审批动作(waiting_review 三态流转)",
)
async def review_ticket(
    ticket_id: str,
    body: AdminReviewRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """审批:approve_close / approve_whitelist / reject_for_reopen(§7.5.2)。

    与原 ``/admin/review`` 行为等价,委托
    :meth:`GovernanceAdminService.review_ticket`,不改变状态机语义。
    """
    result = await asyncio.to_thread(
        admin_svc.review_ticket,
        ticket_id=ticket_id,
        action=body.action,
        admin_id=body.admin_id or ctx.user_id,
        remark=body.remark,
    )
    _raise_on_admin_error(result)
    data = AdminReviewResponse.from_outcome(result)
    return ApiResponse(success=True, data=data.model_dump())