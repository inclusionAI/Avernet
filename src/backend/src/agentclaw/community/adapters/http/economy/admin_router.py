"""Admin router for economy/governance endpoints (AuthPlugin auth).

All admin/ops endpoints under ``/api/economy/governance/admin/*``:

  - /admin/tickets:close          关闭工单(单/多,body ticket_ids 循环 emergency_close)
  - /admin/tickets:close-all      全部关单(dispatch:cancel_pending / close_all_open)
  - /admin/tickets:deliver        按 worker_id 精准投递(不重跑状态机)
  - /admin/whitelist:delete       删除白名单条目
  - /admin/whitelist:bulk-add     批量加白名单
  - /admin/brake (POST)           全局制动 toggle(pause/resume)
  - /admin/brake (GET)            查询制动状态
  - /admin/records:delete         数据维护/清理 (record_daily / notify_log)
  - /admin/trigger-scan           手动触发 cron tick (§7.3)
  - /admin/scan-and-deliver       扫描+投递 (指定收件人, 可 dry-run)

审批能力(review)已迁出至 ``workflow_router``(见
``/api/economy/governance/workflow/*``)。emergency 命名已退场:
真正的制动只是 ``/admin/brake``,工单直关归 ``/admin/tickets:close``。

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
    RecordsDeleteRequest,
    TicketsCloseAllRequest,
    TicketsCloseRequest,
    TicketsDeliverRequest,
    WhitelistBulkAddRequest,
    WhitelistDeleteRequest,
)
from agentclaw.community.api.governance_service import (
    GovernanceAdminServiceProtocol,
    GovernanceBotServiceProtocol,
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
_ScanService = GovernanceBotServiceProtocol


# ---------------------------------------------------------------------------
# Private helpers — keeps handlers short
# ---------------------------------------------------------------------------


def _cron_tick_to_dict(summary: object) -> dict:
    """Serialize a CronTickSummary to the dict shape returned by API."""
    return {
        "run_id": summary.run_id,
        "sent_count": summary.sent_count,
        "failed_count": summary.failed_count,
        "cancelled_count": summary.cancelled_count,
        "reminders_created": summary.reminders_created,
        "schedule_due_count": summary.schedule_due_count,
        "timeout_recovered": summary.timeout_recovered,
        "errors": summary.errors,
        "dry_run": summary.dry_run,
        "duration_seconds": summary.duration_seconds,
    }


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
        # TicketActionOutcome / EmergencyState / BulkOperationResult
        error = getattr(result, "error", None)
        if error:
            error_code = getattr(result, "error_code", "")
            status_code = 404 if error_code == "NOT_FOUND" else 400
            raise HTTPException(status_code=status_code, detail=error)


# ── Tickets: close (单/多) ────────────────────────────────────────────────


@admin_router.post(
    "/admin/tickets:close",
    summary="关闭工单(单/多,body ticket_ids 循环 emergency_close)",
)
async def tickets_close(
    body: TicketsCloseRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Close one or more governance tickets (emergency_close 循环)。

    单条/批量统一入参:handler 循环调 ``admin_svc.emergency_close``(已委托
    ``lifecycle_svc``,关工单 + cancel_pending 由 driver 编排)。禁止直调 repo。
    """
    operator = ctx.user_id

    def _close_all():
        results = []
        for ticket_id in body.ticket_ids:
            result = admin_svc.emergency_close(
                ticket_id=ticket_id,
                admin_id=operator,
                reason=body.reason,
            )
            results.append(result.to_dict())
        return results

    results = await asyncio.to_thread(_close_all)
    return ApiResponse(success=True, data=results)


# ── Tickets: close-all (dispatch 复用状态机收口后的两方法) ──────────────


@admin_router.post(
    "/admin/tickets:close-all",
    summary="全部关单(dispatch:cancel_pending 仅未响应 / close_all_open 全量)",
)
async def tickets_close_all(
    body: TicketsCloseAllRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Close all active governance tickets (emergency bulk)。

    ``only_unresponded=true`` → ``cancel_pending``(仅未响应,EMERGENCY_CLOSED,
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


# ── Tickets: deliver by worker (精准投递,不重跑状态机) ──────────────────


@admin_router.post(
    "/admin/tickets:deliver",
    summary="按 worker_id 精准投递 pending 通知(不重跑状态机)",
)
async def tickets_deliver(
    body: TicketsDeliverRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Deliver pending notifications scoped to one worker_id。

    与 scan-and-deliver(随机批量兜底)职责不同:本端点按 worker 精准取数,
    不跑 cron tick、不重跑状态机(pending 已躺 notify_log)。
    """
    data = await asyncio.to_thread(
        admin_svc.deliver_by_worker,
        worker_id=body.worker_id,
        override_recipient=body.override_recipient,
        dry_run=body.dry_run,
        channel=body.channel,
    )
    return ApiResponse(success=True, data=data)


# ── Whitelist ──────────────────────────────────────────────────────────────


@admin_router.post(
    "/admin/whitelist:delete",
    summary="删除白名单条目",
)
async def delete_whitelist(
    body: WhitelistDeleteRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
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
            result = admin_svc.delete_whitelist_entry(
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
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Bulk whitelist bots — delegates to ``admin_svc.bulk_whitelist``."""
    operator = body.operator or ctx.user_id
    result = await asyncio.to_thread(
        admin_svc.bulk_whitelist,
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
    """Toggle global governance brake: ``enabled=true`` → pause, ``false`` → resume."""
    operator = body.operator or ctx.user_id
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
        return ApiResponse(success=True, data=_cron_tick_to_dict(summary))
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
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Governance management: cron tick → deliver (testing tool).

    Phase 1 (cron tick) runs here; Phase 2-5 (read pending, build payloads,
    send, update DB) are delegated to :meth:`admin_svc.deliver_pending`.
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
            scan_summary = _cron_tick_to_dict(result)
        except Exception:
            log.exception("[scan-and-deliver] Cron tick phase failed")
            scan_summary = {"error": "Cron tick failed — see backend logs"}

    # ---- Phase 2-5: delegate to service ----
    data = await asyncio.to_thread(
        admin_svc.deliver_pending,
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
