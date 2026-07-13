"""Open + business-data router for economy/governance endpoints.

两个端点鉴权方式不同(经核实线上能力 + 调用方可达性):
  - /card-callback            钉钉卡片回调(iframe fetch POST,治理反馈真入口)—
                              cookie/SSO(RequestContext,card iframe 线上已能拿 cookie)
  - /records/offline-batch    ODPS 离线批量写入(§7.2, 增量幂等)— 静态 Bearer
                              token(ODPS pipeline 无用户会话,token 经 SecretResolver:
                              singlebox fallback / prod Mist)

用户自助端点(/notifications*、用户 /whitelist)已删除:无真实用户主动调用场景,
治理反馈真入口是 card-callback。

Admin 端点在 :mod:`agentclaw.community.adapters.http.economy.admin_router`
(/admin/tickets:* / /admin/whitelist:* / /admin/brake / /admin/records:delete /
/admin/trigger-scan / /admin/scan-and-deliver)。
Workflow(正常业务流程)端点在 :mod:`agentclaw.community.adapters.http.economy.workflow_router`
(/workflow/tickets / /workflow/tickets/detail / /workflow/tickets/review)。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Header, HTTPException

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.economy.schemas import (
    ApiResponse,
    CardCallbackIFrameRequest,
    CardCallbackResponse,
    OfflineBatchRequest,
    OfflineBatchResponse,
    RecordProcessResultItem,
)
from agentclaw.community.di import Injected
from agentclaw.community.di.config import EconomyInternalToken
from agentclaw.community.api.governance_service import (
    GovernanceFeedbackServiceProtocol,
    GovernanceRecordProcessProtocol,
)


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public router — /api/economy/governance
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/economy/governance", tags=["economy-governance"])

# Lazy imports to avoid circular dependency at module level
_FeedbackService = GovernanceFeedbackServiceProtocol
_OfflineBatchSvc = GovernanceRecordProcessProtocol


async def verify_economy_internal_token(
    authorization: str | None = Header(None, description="Bearer <token>"),
    token_cfg: EconomyInternalToken = Injected(EconomyInternalToken),
) -> None:
    """Raise 401 unless the request carries the configured offline-batch token.

    Static Bearer token gate for offline-batch — its callers (ODPS pipeline /
    upload_governance_data.py) have no user session (cookie/SSO unreachable),
    so API-friendly static token is the only viable auth. Token value is
    resolved by ``EconomyGovernanceModule`` via SecretResolver (singlebox
    fallback / prod Mist). ``Header(None)`` so a missing header yields a
    uniform 401 (not a 422 leaking "header required").
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]
    # No token configured = feature off / secret unresolvable → reject all.
    if not token_cfg.value or token != token_cfg.value:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.post("/card-callback", summary="卡片回调 (iframe fetch POST)")
async def card_callback(
    body: CardCallbackIFrameRequest,
    ctx: RequestContext = Depends(get_request_context),
    feedback_svc: _FeedbackService = Injected(_FeedbackService),
) -> ApiResponse:
    """Card iframe fetch POST callback.

    Auth: cookie/SSO via ``RequestContext`` (card iframe now carries the
    browser session cookie — verified online). The authenticated user
    (``ctx.user_id``) is the feedback actor; owner is still resolved from
    notify_log inside ``resolve()`` when needed.
    """
    # Parse repair_deadline
    repair_deadline_dt = None
    if body.repair_deadline:
        try:
            from datetime import datetime as _dt
            repair_deadline_dt = _dt.fromisoformat(body.repair_deadline)
        except ValueError:
            pass

    result = await asyncio.to_thread(
        feedback_svc.resolve,
        notification_id=body.notification_id,
        response=body.response,
        user_id=ctx.user_id,  # authenticated feedback actor
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
    summary="离线批量写入 (§7.2, 增量幂等)",
)
async def offline_batch(
    body: OfflineBatchRequest,
    partial_svc: _OfflineBatchSvc = Injected(_OfflineBatchSvc),
    _: None = Depends(verify_economy_internal_token),
) -> ApiResponse:
    """Upsert ODPS pipeline results via process_offline_batch (§7.2).

    增量幂等(靠内部守卫,非入口去重):同 worker 重提同/旧 dt_version 的记录,已有
    活跃工单会被 dt_version 守卫 skip(不刷新、不重发,仅 audit);新 worker 创建
    新工单+首次通知。即「已存在的不动、新的能进」——例如先提 5 条 dt=0711,再提
    7 条含同 5 条 + 2 新 worker,则原 5 条不变、2 条创新单。靠 process_record 的
    active ticket 检查 + dt 守卫(≤existing 则 skip) + cooldown 这套既有内部守卫
    实现,不引入入口去重。响应返回 batch_id + run_id 供上游对账。

    单条记录失败不阻断整批(续跑),失败记录在 upsert_results 中以 action="error"
    + worker_key + reason 回传,errors 计数同步累加。

    Auth: static Bearer token (``verify_economy_internal_token``) — called by
    offline ODPS pipeline with no user session (cookie/SSO unreachable). Token
    resolved via SecretResolver (singlebox fallback / prod Mist secret).

    ``process_offline_batch`` is synchronous (loop over records doing
    per-record DB upserts); running it inline in an ``async def`` would
    occupy the event-loop worker for the whole batch and starve every
    other request on that worker. Offload the synchronous batch to the
    default thread pool so the event loop stays responsive, matching the
    pattern used by ``desktop_bot``/``task_queue``/``channel``.
    """
    log.info(
        "[OfflineBatch] POST /records/offline-batch: batch_id=%s, "
        "dt_version=%s, total_count=%d, actual_records=%d",
        body.batch_id, body.dt_version, body.total_count, len(body.records),
    )

    result = await asyncio.to_thread(
        partial_svc.process_offline_batch,
        [r.to_record() for r in body.records],
        batch_id=body.batch_id,
        dt_version=body.dt_version,
        total_count=body.total_count,
        dry_run=False,  # offline batch always writes
    )

    # Action distribution summary
    action_counts: dict[str, int] = {}
    for pr in result.upsert_results:
        action_counts[pr.action] = action_counts.get(pr.action, 0) + 1
    log.info(
        "[OfflineBatch] Response: batch_id=%s, run_id=%s, "
        "total=%d, errors=%d, quality_skipped=%s, actions=%s",
        result.batch_id, result.run_id,
        result.total_records, result.errors,
        result.batch_quality_skipped, action_counts,
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
# Re-export workflow_router (app.py 经此 import workflow router)
# ---------------------------------------------------------------------------

from agentclaw.community.adapters.http.economy.workflow_router import (  # noqa: E402
    workflow_router as workflow_router_export,
)


__all__ = ["router", "workflow_router_export"]
