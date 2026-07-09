"""Admin router for economy/governance endpoints (AuthPlugin auth).

All admin/ops endpoints under ``/api/economy/governance/admin/*``:

  - /admin/records/delete           应急删除 (record_daily / notify_log)
  - /admin/whitelist/delete         删除白名单条目
  - /admin/review                   审批审批单 (§7.5.2)
  - /admin/pause                    暂停审批单 (§7.5.1)
  - /admin/emergency-close          紧急关闭审批单 (§6.3)
  - /admin/trigger-scan             手动触发 cron tick (§7.3)
  - /admin/emergency (POST)         紧急制动 (pause/resume/bulk-whitelist/cancel-pending)
  - /admin/emergency (GET)          查询紧急状态
  - /admin/scan-and-deliver         扫描+投递 (指定收件人, 可 dry-run)

DB logic is fully delegated to :class:`GovernanceAdminService` — this router
never opens ORM sessions or queries models directly.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.adapters.http.economy.schemas import (
    AdminEmergencyCloseRequest,
    AdminPauseRequest,
    AdminReviewRequest,
    AdminReviewResponse,
    ApiResponse,
    EmergencyRequest,
    EmergencyStateResponse,
    RecordsDeleteRequest,
    WhitelistDeleteRequest,
)
from agentclaw.community.core.economy.governance.domain.enums import GovernanceStatus
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


# ── Records delete (emergency) ────────────────────────────────────────────


@admin_router.post(
    "/admin/records/delete",
    summary="应急删除 (record_daily / notify_log)",
)
async def delete_records(
    body: RecordsDeleteRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Emergency delete for record_daily or notify_log.

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


# ── Whitelist delete ───────────────────────────────────────────────────────


@admin_router.post(
    "/admin/whitelist/delete",
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


# ── Admin: review / pause / emergency-close ───────────────────────────────


@admin_router.post(
    "/admin/review",
    summary="审批审批单 (§7.5.2)",
)
async def admin_review(
    body: AdminReviewRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Admin review: waiting_review → closed (§7.5.2)."""
    result = await asyncio.to_thread(
        admin_svc.review_ticket,
        ticket_id=body.ticket_id,
        action=body.action,
        admin_id=body.admin_id or ctx.user_id,
        remark=body.remark,
    )
    _raise_on_admin_error(result)
    return ApiResponse(
        success=True,
        data=AdminReviewResponse(
            ticket_id=result.ticket_id,
            governance_status=result.status.value if isinstance(result.status, GovernanceStatus) else str(result.status or ""),
            close_reason=result.close_reason,
        ).model_dump(),
    )


@admin_router.post(
    "/admin/pause",
    summary="暂停审批单 (§7.5.1)",
)
async def admin_pause(
    body: AdminPauseRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Admin pause: open/scheduled → waiting_review (§7.5.1)."""
    result = await asyncio.to_thread(
        admin_svc.pause_ticket,
        ticket_id=body.ticket_id,
        admin_id=body.admin_id or ctx.user_id,
        reason=body.reason,
    )
    _raise_on_admin_error(result)
    return ApiResponse(success=True, data=result.to_dict())


@admin_router.post(
    "/admin/emergency-close",
    summary="紧急关闭审批单 (§6.3)",
)
async def admin_emergency_close(
    body: AdminEmergencyCloseRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Emergency close: any non-closed ticket → closed, no cooldown."""
    result = await asyncio.to_thread(
        admin_svc.emergency_close,
        ticket_id=body.ticket_id,
        admin_id=body.admin_id or ctx.user_id,
        reason=body.reason,
    )
    _raise_on_admin_error(result)
    return ApiResponse(success=True, data=result.to_dict())


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


# ── Emergency brake ───────────────────────────────────────────────────────


@admin_router.post(
    "/admin/emergency",
    summary="紧急制动",
)
async def emergency_action(
    body: EmergencyRequest,
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Emergency brake / admin actions: pause / resume / bulk-whitelist / cancel-pending / close-all-open."""
    if body.action == "pause":
        await asyncio.to_thread(admin_svc.pause, reason=body.reason, operator=body.operator)
        return ApiResponse(success=True, message="Paused")

    if body.action == "resume":
        await asyncio.to_thread(admin_svc.resume, reason=body.reason, operator=body.operator)
        return ApiResponse(success=True, message="Resumed")

    if body.action == "bulk-whitelist":
        if not body.bot_ids:
            raise HTTPException(status_code=400, detail="bot_ids required for bulk-whitelist")
        result = await asyncio.to_thread(
            admin_svc.bulk_whitelist,
            bot_ids=body.bot_ids,
            reason=body.reason,
            operator=body.operator,
        )
        return ApiResponse(success=True, data=result)

    if body.action == "cancel-pending":
        result = await asyncio.to_thread(
            admin_svc.cancel_pending,
            reason=body.reason,
            operator=body.operator,
        )
        return ApiResponse(success=True, data=result.to_dict())

    if body.action == "close-all-open":
        result = await asyncio.to_thread(
            admin_svc.close_all_open,
            reason=body.reason,
            operator=body.operator,
        )
        return ApiResponse(success=True, data=result.to_dict())

    raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")


@admin_router.get(
    "/admin/emergency",
    summary="查询紧急状态",
)
async def get_emergency_state(
    ctx: RequestContext = Depends(get_request_context),
    admin_svc: _AdminSvc = Injected(_AdminSvc),
) -> ApiResponse:
    """Query current emergency state."""
    state = await asyncio.to_thread(admin_svc.get_state)
    return ApiResponse(
        success=True,
        data=EmergencyStateResponse(**state.to_dict()).model_dump(),
    )


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
