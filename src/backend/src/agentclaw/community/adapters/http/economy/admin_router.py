"""Admin router for economy/governance endpoints (AuthPlugin auth).

All admin/ops endpoints under ``/api/economy/governance/admin/*``:

  - /admin/tickets:deliver        按 worker_id 精准投递(不重跑状态机)
  - /admin/whitelist:delete       删除白名单条目
  - /admin/whitelist:bulk-add     批量加白名单
  - /admin/whitelist (GET)        白名单只读分页列表(全量/筛选/默认排除过期)
  - /admin/brake (POST)           全局制动 toggle(pause/resume)
  - /admin/brake (GET)            查询制动状态
  - /admin/records:delete         数据维护/清理 (record_daily / notify_log)
  - /admin/trigger-scan           手动触发 cron tick (§7.3)
  - /admin/scan-and-deliver       扫描+投递 (指定收件人, 可 dry-run)

审批能力(review)已迁出至 ``workflow_router``。关单能力(tickets:close /
tickets:close-all + admin_close/cancel_pending/close_all_open service 方法)
亦按"工单运营 vs 运维"边界迁至 workflow_router / workflow_service(见
``/api/economy/governance/workflow/*``)。本 router 留 deliver/brake/records/
scan/whitelist 等纯运维。

路径风格:全 body/query 驱动,零 path 参数。

DB logic is fully delegated to :class:`GovernanceAdminService` — this router
never opens ORM sessions or queries models directly.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.adapters.http.economy.schemas import (
    ApiResponse,
    BrakeStateResponse,
    BrakeToggleRequest,
    GovernanceRecordInput,
    RecordsDeleteRequest,
    RemindRequest,
    TicketsDeliverRequest,
    WhitelistBulkAddRequest,
    WhitelistDeleteRequest,
)
from agentclaw.community.api.governance_service import (
    GovernanceAdminServiceProtocol,
    GovernanceBotServiceProtocol,
    GovernanceDeliveryServiceProtocol,
    GovernanceWhitelistServiceProtocol,
)
from agentclaw.community.di import Injected


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Admin router — /api/economy/governance (AuthPlugin auth)
# ---------------------------------------------------------------------------

admin_router = APIRouter(
    prefix="/api/economy/governance",
    tags=["economy-governance-admin"],
)

_AdminSvc = GovernanceAdminServiceProtocol
_DeliverySvc = GovernanceDeliveryServiceProtocol
_ScanService = GovernanceBotServiceProtocol
_WhitelistSvc = GovernanceWhitelistServiceProtocol


# ---------------------------------------------------------------------------
# Private helpers — keeps handlers short
# ---------------------------------------------------------------------------


def _raise_on_admin_error(result: dict | object) -> None:
    """Raise HTTPException if the admin-service result contains an error.

    Accepts both ``dict`` (legacy) and ``TicketActionOutcome`` dataclass.
    """
    if isinstance(result, dict):
        if "error" in result:
            error_code = result.get("error_code", "")
            status_code = 404 if error_code == "NOT_FOUND" else 400
            raise HTTPException(status_code=status_code, detail=result["error"])
    else:
        # TicketActionOutcome / BrakeState / BulkOperationResult
        error = getattr(result, "error", None)
        if error:
            error_code = getattr(result, "error_code", "")
            status_code = 404 if error_code == "NOT_FOUND" else 400
            raise HTTPException(status_code=status_code, detail=error)


# ── Tickets: deliver by worker (精准投递,不重跑状态机) ──────────────────


@admin_router.post(
    "/admin/tickets:deliver",
    summary="按 worker_id 精准投递 pending 通知(不重跑状态机)",
)
async def tickets_deliver(
    body: TicketsDeliverRequest,
    ctx: RequestContext = Depends(get_request_context),
    delivery_svc: _DeliverySvc = Injected(_DeliverySvc),
) -> ApiResponse:
    """Deliver pending notifications scoped to one worker_id。

    与 scan-and-deliver(随机批量兜底)职责不同:本端点按 worker 精准取数,
    不跑 cron tick、不重跑状态机(pending 已躺 notify_log)。
    """
    data = await asyncio.to_thread(
        delivery_svc.deliver_by_worker,
        worker_id=body.worker_id,
        override_recipient=body.override_recipient,
        dry_run=body.dry_run,
        channel=body.channel,
    )
    return ApiResponse(success=True, data=data)


# ── Tickets: remind (手动补发 reminder,跳过 remind_at 等待) ──────────────


@admin_router.post(
    "/admin/tickets:remind",
    summary="手动补发 reminder(worker_id 找 active 工单,立即发送)",
)
async def tickets_remind(
    body: RemindRequest,
    ctx: RequestContext = Depends(get_request_context),
    delivery_svc: _DeliverySvc = Injected(_DeliverySvc),
) -> ApiResponse:
    """手动补发 reminder:按 worker_id 找 active 工单,立即创建+发送 reminder 通知。

    跳过 remind_at 等待(不等 cron tick)。无 active 工单 → 400。
    """
    try:
        result = await asyncio.to_thread(
            delivery_svc.create_and_send_reminder,
            worker_id=body.worker_id,
            operator=ctx.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(success=True, data=result)


# ── Tickets: offline-renew (强制用离线 record 换新,跳过 7天限制) ─────────


@admin_router.post(
    "/admin/tickets:offline-renew",
    summary="强制换新(用离线 record 关老+建新 first_send,跳过 7天)",
)
async def tickets_offline_renew(
    body: GovernanceRecordInput,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """强制换新:接收一条离线治理 record,无视 gmt_create 7天 + dt_version guard,
    强制关掉该 worker 的 active 工单(stale_replaced) + 建新 first_send。

    admin 手动操作,用于立即应用最新数据给 owner 发通知。
    """
    record = body.to_record()
    result = await asyncio.to_thread(
        admin_svc.force_renew_with_record,
        record=record,
        operator=ctx.user_id,
    )
    return ApiResponse(success=True, data=result)


# ── Whitelist ──────────────────────────────────────────────────────────────


@admin_router.post(
    "/admin/whitelist:delete",
    summary="删除白名单条目",
)
async def delete_whitelist(
    body: WhitelistDeleteRequest,
    ctx: RequestContext = Depends(get_request_context),
    whitelist_svc: _WhitelistSvc = Injected(_WhitelistSvc),
) -> ApiResponse:
    """Delete governance whitelist entries by (bot_id, owner_id) pairs."""
    if not body.bot_owner_pairs or len(body.bot_owner_pairs) == 0:
        raise HTTPException(
            status_code=400,
            detail="bot_owner_pairs is required",
        )

    def _delete_all():
        results = []
        for pair in body.bot_owner_pairs:
            result = whitelist_svc.delete_whitelist_entry(
                bot_id=pair.bot_id,
                owner_id=pair.owner_id,
                reason=body.reason,
                operator=ctx.user_id,
            )
            results.append(result)
        return results

    results = await asyncio.to_thread(_delete_all)
    return ApiResponse(success=True, data=results)


@admin_router.post(
    "/admin/whitelist:bulk-add",
    summary="批量加白名单",
)
async def whitelist_bulk_add(
    body: WhitelistBulkAddRequest,
    ctx: RequestContext = Depends(get_request_context),
    whitelist_svc: _WhitelistSvc = Injected(_WhitelistSvc),
) -> ApiResponse:
    """Bulk whitelist bots — delegates to ``whitelist_svc.bulk_whitelist``."""
    operator = ctx.user_id
    result = await asyncio.to_thread(
        whitelist_svc.bulk_whitelist,
        bot_ids=body.bot_ids,
        reason=body.reason,
        operator=operator,
    )
    return ApiResponse(success=True, data=result)


# ── Brake (全局制动) ──────────────────────────────────────────────────────


@admin_router.post(
    "/admin/brake",
    summary="全局制动 toggle(pause/resume)",
)
async def brake_toggle(
    body: BrakeToggleRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Toggle global governance brake: ``enabled=true`` → pause, ``false`` → resume.

    审计操作人取自鉴权上下文 ``ctx.user_id``(不允许 body 顶替)。
    """
    operator = ctx.user_id
    if body.enabled:
        await asyncio.to_thread(admin_svc.pause, reason=body.reason, operator=operator)
        return ApiResponse(success=True, message="Paused")
    await asyncio.to_thread(admin_svc.resume, reason=body.reason, operator=operator)
    return ApiResponse(success=True, message="Resumed")


@admin_router.get(
    "/admin/brake",
    summary="查询制动状态",
)
async def brake_state(
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Query current governance brake state."""
    del ctx  # RequestContext 仅用于走 AuthPlugin 鉴权链路
    state = await asyncio.to_thread(admin_svc.get_state)
    return ApiResponse(
        success=True,
        data=BrakeStateResponse(**state.to_dict()).model_dump(),
    )


# ── Whitelist read (只读分页列表) ─────────────────────────────────────────


@admin_router.get(
    "/admin/whitelist",
    summary="白名单只读分页列表(全量/按 owner·bot·类型筛选,默认排除过期)",
)
async def list_whitelist(
    ctx: RequestContext = Depends(get_request_context),
    whitelist_svc: _WhitelistSvc = Injected(_WhitelistSvc),
    owner_id: str | None = Query(None, description="按负责人 owner_id 精确筛选"),
    bot_id: str | None = Query(None, description="按 bot_id 精确筛选"),
    whitelist_type: str = Query(
        "governance", description="白名单类型,缺省 governance",
    ),
    include_expired: bool = Query(
        False, description="True=返回含已过期条目的全量视图",
    ),
    limit: int = Query(50, ge=1, le=200, description="分页上限 1~200"),
    offset: int = Query(0, ge=0, description="分页偏移"),
) -> ApiResponse:
    """只读分页白名单列表(含最近工单维度字段)。

    复用 ``whitelist_service.list_all_with_ticket_meta``:白单元数据
    (bot_id/owner_id/whitelist_type/source/reason/created_by/expires_at
    /gmt_create/gmt_modified)+ 最近一条工单维度叠加(bot_name/owner_name
    /token_baseline/expected_token_saving/hit_dimensions/saving_ratio
    /latest_decision/latest_ticket_gmt_create)。default 排除已过期项。
    只读:无 audit、无副作用;鉴权走 ``get_request_context``。无对应工单的白单
    叠加字段为 None,条目保留。
    """
    del ctx  # RequestContext 仅用于走 AuthPlugin 鉴权链路
    items, total = await asyncio.to_thread(
        whitelist_svc.list_all_with_ticket_meta,
        whitelist_type=whitelist_type,
        owner_id=owner_id,
        bot_id=bot_id,
        include_expired=include_expired,
        limit=limit,
        offset=offset,
    )
    return ApiResponse(
        success=True,
        data={
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    )


# ── Records delete (数据维护/清理) ────────────────────────────────────────


@admin_router.post(
    "/admin/records:delete",
    summary="数据维护/清理 (record_daily / notify_log)",
)
async def delete_records(
    body: RecordsDeleteRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Maintenance delete for record_daily or notify_log.

    ``dry_run`` defaults to True — only counts matches without deleting.
    Set ``dry_run=false`` to actually delete.
    """
    if not any([body.dt_versions, body.ids, body.notification_ids]):
        raise HTTPException(
            status_code=400,
            detail="At least one of dt_versions / ids / notification_ids is required",
        )

    if body.table not in ("record_daily", "notify_log"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown table: {body.table!r}. Use 'record_daily' or 'notify_log'",
        )

    data = await asyncio.to_thread(admin_svc.delete_records, body.model_dump(), operator=ctx.user_id)
    return ApiResponse(success=True, data=data)


# ── Tickets: delete-cascade 已迁至 workflow_router(/workflow/tickets:delete-cascade,
#    工单运营边界)2026-07-17。─────────────────────────────────────────────


# ── Trigger scan / cron tick ──────────────────────────────────────────────


@admin_router.post(
    "/admin/trigger-scan",
    summary="手动触发 cron tick (§7.3)",
)
async def trigger_scan(
    ctx: RequestContext = Depends(get_request_context),
    dry_run: bool | None = Query(None, description="Override config.dry_run"),
    scan_svc: _ScanService = Injected(_ScanService),
) -> ApiResponse:
    """Manually trigger a governance cron tick (§7.3)."""
    try:
        summary = await asyncio.to_thread(scan_svc.process_cron_tick, dry_run=dry_run)
        return ApiResponse(success=True, data=summary.to_dict())
    except Exception:
        log.exception("[EconomyGovernance] trigger-scan failed")
        return ApiResponse(success=False, message="Scan failed", error_code="SCAN_ERROR")


# ── Scan and deliver (testing tool) ───────────────────────────────────────


@admin_router.post(
    "/admin/scan-and-deliver",
    summary="扫描+投递通知 (可指定收件人, 可 dry-run)",
)
async def scan_and_deliver(
    ctx: RequestContext = None,  # TODO: revert — temporarily disabled auth for dev testing
    override_recipient: str = Query(
        ...,
        pattern=r"^\d{4,10}$",
        description="覆盖收件人工号 (纯数字 4~10 位), 所有通知只发给此人工号",
    ),
    dry_run: bool = Query(
        True, description="dry-run 模式: 只展示待投递内容, 不发钉钉",
    ),
    max_send: int = Query(
        1, description="最大发送条数, 0=不限制",
    ),
    skip_scan: bool = Query(
        False, description="跳过 scan 阶段, 直接投递已有 pending 通知",
    ),
    scan_dry_run: bool = Query(
        False, description="scan 阶段使用 dry_run (仅审计不入库通知)",
    ),
    channel: str = Query(
        "auto", description="发送通道: auto(跟随DB记录)|markdown|tc_card",
    ),
    scan_svc: _ScanService = Injected(_ScanService),
    delivery_svc: _DeliverySvc = Injected(_DeliverySvc),
) -> ApiResponse:
    """Governance management: cron tick → deliver (testing tool).

    Phase 1 (cron tick) runs here; Phase 2-5 (read pending, build payloads,
    send, update DB) are delegated to :meth:`delivery_svc.deliver_pending`.
    """
    # Safety guard: dry_run=false requires a valid numeric staff_id
    if not dry_run and not override_recipient.isdigit():
        raise HTTPException(
            status_code=400,
            detail="override_recipient must be a numeric staff ID when dry_run=false",
        )

    # ---- Phase 1: Cron tick (unless skip_scan) ----
    scan_summary: dict = {}
    if not skip_scan:
        try:
            result = await asyncio.to_thread(scan_svc.process_cron_tick, dry_run=scan_dry_run)
            scan_summary = result.to_dict()
        except Exception:
            log.exception("[scan-and-deliver] Cron tick phase failed")
            scan_summary = {"error": "Cron tick failed — see backend logs"}

    # ---- Phase 2-5: delegate to service ----
    data = await asyncio.to_thread(
        delivery_svc.deliver_pending,
        scan_svc=scan_svc,
        override_recipient=override_recipient,
        dry_run=dry_run,
        max_send=max_send,
        channel=channel,
        skip_scan=skip_scan,
        scan_dry_run=scan_dry_run,
    )

    # Merge Phase 1 result
    data["scan"] = scan_summary

    # Handle "no pending" early return
    if data["total"] == 0 and not scan_summary:
        return ApiResponse(
            success=True,
            data=data,
            message="No pending notifications to deliver",
        )

    return ApiResponse(success=True, data=data)
