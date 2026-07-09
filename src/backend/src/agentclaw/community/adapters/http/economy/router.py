"""Public + business-data router for economy/governance endpoints.

Public (7 endpoints — user-facing):
  - /notifications            查询待处理通知
  - /notifications/history    历史反馈记录
  - /notifications/{id}       通知详情
  - /notifications/{id}/resolve  用户反馈
  - /whitelist/batch          批量加白
  - /whitelist                查询加白列表
  - /card-callback            卡片回调 (iframe fetch POST)

Business (1 endpoint — data ingestion, no admin auth):
  - /records/offline-batch    离线批量写入 (§7.2)

Admin endpoints are in :mod:`agentclaw.community.adapters.http.economy.admin_router`.
  - /admin/records/delete         应急删除
  - /admin/review                 管理员审核
  - /admin/pause                  管理员暂停工单
  - /admin/emergency-close        管理员紧急关闭
  - /admin/trigger-scan           手动触发 cron tick
  - /admin/emergency              紧急制动 / 查询状态
  - /admin/scan-and-deliver       扫描+投递
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.adapters.http.economy.schemas import (
    ApiResponse,
    CardCallbackIFrameRequest,
    CardCallbackResponse,
    GovernanceNotifyResolveRequest,
    GovernanceNotifyResolveResponse,
    OfflineBatchRequest,
    OfflineBatchResponse,
    RecordProcessResultItem,
    WhitelistBatchRequest,
    WhitelistBatchResponse,
)
from agentclaw.community.api.governance_service import (
    GovernanceFeedbackServiceProtocol,
    GovernanceRecordProcessProtocol,
    GovernanceWhitelistProtocol,
)
from agentclaw.community.di import Injected


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public router — /api/economy/governance
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/economy/governance", tags=["economy-governance"])

# Lazy imports to avoid circular dependency at module level
_FeedbackService = GovernanceFeedbackServiceProtocol
_WhitelistRepo = GovernanceWhitelistProtocol
_OfflineBatchSvc = GovernanceRecordProcessProtocol


@router.get("/notifications", summary="查询当前用户待处理通知")
async def list_pending_notifications(
    ctx: RequestContext = Depends(get_request_context),
    feedback_svc: _FeedbackService = Injected(_FeedbackService),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List pending (open/muted) notifications for the current user."""
    owner_id = ctx.user_id
    items = feedback_svc.list_pending(owner_id, limit=limit, offset=offset)
    return ApiResponse(success=True, data=items)


@router.get("/notifications/history", summary="历史反馈记录")
async def list_history_notifications(
    ctx: RequestContext = Depends(get_request_context),
    feedback_svc: _FeedbackService = Injected(_FeedbackService),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List closed/expired notifications for the current user."""
    owner_id = ctx.user_id
    items = feedback_svc.list_history(owner_id, limit=limit, offset=offset)
    return ApiResponse(success=True, data=items)


@router.get("/notifications/{notification_id}", summary="通知详情")
async def get_notification_detail(
    notification_id: str,
    ctx: RequestContext = Depends(get_request_context),
    feedback_svc: _FeedbackService = Injected(_FeedbackService),
) -> ApiResponse:
    """Get a single notification by ID."""
    owner_id = ctx.user_id
    item = feedback_svc.get_notification(notification_id, owner_id)
    if not item:
        raise HTTPException(status_code=404, detail="Notification not found")
    return ApiResponse(success=True, data=item)


@router.post("/notifications/{notification_id}/resolve", summary="用户反馈")
async def resolve_notification(
    notification_id: str,
    body: GovernanceNotifyResolveRequest,
    ctx: RequestContext = Depends(get_request_context),
    feedback_svc: _FeedbackService = Injected(_FeedbackService),
) -> ApiResponse:
    """User feedback: optimized / need_time / dispute / whitelist."""
    owner_id = ctx.user_id

    from datetime import datetime as _dt
    repair_deadline = None
    if body.repair_deadline:
        try:
            repair_deadline = _dt.fromisoformat(body.repair_deadline)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid repair_deadline format") from None

    result = feedback_svc.resolve(
        notification_id=notification_id,
        response=body.response,
        user_id=owner_id,
        remark=body.remark,
        source="http_api",
        repair_deadline=repair_deadline,
        feedback_payload=body.feedback_payload,
    )

    if not result.success:
        error_code = getattr(result, "error_code", None) or ""
        if error_code == "NOT_FOUND":
            status_code = 404
        else:
            status_code = 400
        raise HTTPException(status_code=status_code, detail=result.error)

    return ApiResponse(
        success=True,
        data=GovernanceNotifyResolveResponse(
            notification_id=result.notification_id,
            governance_status=result.governance_status,
            close_reason=result.close_reason,
            mute_until=result.mute_until.isoformat() if result.mute_until else None,
            ticket_id=getattr(result, "ticket_id", "") or "",
        ).model_dump(),
    )


@router.post("/whitelist/batch", summary="批量加白")
async def batch_whitelist(
    body: WhitelistBatchRequest,
    ctx: RequestContext = Depends(get_request_context),
    whitelist_svc: _WhitelistRepo = Injected(_WhitelistRepo),
) -> ApiResponse:
    """Batch-add bots to governance whitelist."""
    owner_id = ctx.user_id
    entries = [e.model_dump() for e in body.entries]

    result = whitelist_svc.batch_add(
        entries=entries,
        created_by=owner_id,
        whitelist_type="governance",
        source=body.source,
    )
    return ApiResponse(
        success=True,
        data=WhitelistBatchResponse(**result).model_dump(),
    )


@router.get("/whitelist", summary="查询加白列表")
async def list_whitelist(
    ctx: RequestContext = Depends(get_request_context),
    whitelist_svc: _WhitelistRepo = Injected(_WhitelistRepo),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List governance whitelist entries for the current user."""
    owner_id = ctx.user_id
    items = whitelist_svc.list_all(
        owner_id=owner_id,
        whitelist_type="governance",
        limit=limit,
        offset=offset,
    )
    return ApiResponse(success=True, data=items)


@router.post("/card-callback", summary="卡片回调 (iframe fetch POST)")
async def card_callback(
    body: CardCallbackIFrameRequest,
    feedback_svc: _FeedbackService = Injected(_FeedbackService),
) -> ApiResponse:
    """Card iframe fetch POST callback.

    No RequestContext auth: DingTalk card iframe has no SSO cookie.
    Owner identity is resolved from the notification record in DB
    (notification_id is an unguessable UUID, providing sufficient security).
    """
    # Parse repair_deadline
    repair_deadline_dt = None
    if body.repair_deadline:
        try:
            from datetime import datetime as _dt
            repair_deadline_dt = _dt.fromisoformat(body.repair_deadline)
        except ValueError:
            pass

    result = feedback_svc.resolve(
        notification_id=body.notification_id,
        response=body.response,
        user_id="",  # empty → resolve() reads owner_id from notify_log
        remark=body.remark,
        source="card_callback",
        repair_deadline=repair_deadline_dt,
        feedback_payload=body.feedback_payload,
    )

    if not result.success:
        _ERROR_STATUS_MAP = {
            "NOT_FOUND": 404,
            "INVALID_RESPONSE": 400,
            "MISSING_REMARK": 400,
            "MISSING_REPAIR_DEADLINE": 400,
            "INVALID_FEEDBACK_PAYLOAD": 400,
            "DB_ERROR": 500,
        }
        http_status = _ERROR_STATUS_MAP.get(result.error_code or "", 400)
        raise HTTPException(
            status_code=http_status,
            detail=result.error or "Callback error",
        )

    resp = CardCallbackResponse.from_result(result)
    return ApiResponse(success=True, data=resp.model_dump())


# ── Business-data endpoints (data ingestion) ──────────────────────────────


@router.post(
    "/records/offline-batch",
    summary="离线批量写入 (§7.2)",
)
async def offline_batch(
    body: OfflineBatchRequest,
    partial_svc: _OfflineBatchSvc = Injected(_OfflineBatchSvc),
) -> ApiResponse:
    """Upsert ODPS pipeline results via process_offline_batch (§7.2).

    No auth: called by offline ODPS pipeline (no user session).

    ``process_offline_batch`` is synchronous (loop over records doing
    per-record DB upserts); running it inline in an ``async def`` would
    occupy the event-loop worker for the whole batch and starve every
    other request on that worker. Offload the synchronous batch to the
    default thread pool so the event loop stays responsive, matching the
    pattern used by ``desktop_bot``/``task_queue``/``channel``.
    """
    result = await asyncio.to_thread(
        partial_svc.process_offline_batch,
        body.records,
        batch_id=body.batch_id,
        dt_version=body.dt_version,
        total_count=body.total_count,
        dry_run=False,  # offline batch always writes
    )

    return ApiResponse(
        success=True,
        data=OfflineBatchResponse(
            batch_id=result.batch_id,
            run_id=result.run_id,
            total_records=result.total_records,
            upsert_results=[
                RecordProcessResultItem(
                    worker_key=pr.worker_key,
                    entered_governance_scope=pr.entered_governance_scope,
                    action=pr.action,
                    reason=pr.reason,
                    ticket_id=pr.ticket_id,
                    notification_md_preview=pr.notification_md_preview,
                )
                for pr in result.upsert_results
            ],
            batch_quality_skipped=result.batch_quality_skipped,
            batch_quality_skip_reasons=result.batch_quality_skip_reasons,
            errors=result.errors,
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Re-export admin_router for backward compatibility (app.py imports from here)
# ---------------------------------------------------------------------------

from agentclaw.community.adapters.http.economy.admin_router import (  # noqa: E402
    admin_router as internal_router,
)


__all__ = ["router", "internal_router"]
