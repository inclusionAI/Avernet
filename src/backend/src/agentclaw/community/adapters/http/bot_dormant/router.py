"""Router for bot dormant reactivation and whitelist endpoints.

POST /api/bots/{bot_id}/activate
POST /api/bots/dormant-whitelist/batch
"""

from fastapi import APIRouter, Depends, Path, Query

from agentclaw.community.adapters.http.bot_dormant.schemas import (
    ActivateBotResponse,
    ApiResponse,
    WhitelistBatchRequest,
    WhitelistBatchResponse,
)
from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.core.bot_dormant.activate_service import ActivateBotService, InvalidBotStateError
from agentclaw.community.core.bot_dormant.whitelist_service import WhitelistService
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


logger = get_logger()
router = APIRouter(prefix="/api/bots", tags=["bot-dormant"])


@router.post("/{bot_id}/activate", response_model=ApiResponse)
async def activate_bot(
    bot_id: str = Path(...),
    owner_id: str | None = Query(None),
    ctx: RequestContext = Depends(get_request_context),
    service: ActivateBotService = Injected(ActivateBotService),
) -> ApiResponse:
    """Reactivate a RECYCLED bot.

    - ``owner_id`` (query param): owner user_id when the operator is an admin
      acting on behalf of another user. Defaults to the authenticated operator.
    """
    operator_id = ctx.user_id
    if not operator_id or operator_id == "anonymous":
        return ApiResponse(
            success=False, message="无法获取认证用户信息", error_code=401
        )
    user_id = owner_id or operator_id
    try:
        result = service.activate(
            bot_id=bot_id, user_id=user_id, nick_name=ctx.nick_name
        )
        return ApiResponse(success=True, data=ActivateBotResponse(**result))
    except InvalidBotStateError as e:
        return ApiResponse(success=False, message=str(e), error_code=400)
    except Exception:
        logger.exception("[activate] unexpected error bot_id=%s", bot_id)
        return ApiResponse(
            success=False, message="Internal server error", error_code=500
        )


@router.post("/dormant-whitelist/batch", response_model=ApiResponse)
async def whitelist_batch(
    body: WhitelistBatchRequest,
    ctx: RequestContext = Depends(get_request_context),
    service: WhitelistService = Injected(WhitelistService),
) -> ApiResponse:
    """Batch-add bots to the dormant whitelist.

    Bots on the whitelist are excluded from the dormant-scan candidate set.
    Duplicate entries are skipped silently (idempotent).

    Auth note: only authenticated operators may call this endpoint.
    Fine-grained role check (ops/admin staff-id list) is TBD pending
    auth framework alignment.
    """
    if not ctx.user_id or ctx.user_id == "anonymous":
        return ApiResponse(success=False, message="无认证", error_code=401)
    result = service.batch_add(
        [e.dict() for e in body.entries], created_by=ctx.user_id
    )
    return ApiResponse(success=True, data=WhitelistBatchResponse(**result))


# ---------------------------------------------------------------------------
# Internal endpoints (Bearer-token auth, for in-container notify sender)
# ---------------------------------------------------------------------------
from fastapi import HTTPException  # noqa: E402

from agentclaw.community.adapters.http.bot_dormant.auth import verify_dormant_internal_token  # noqa: E402
from agentclaw.community.adapters.http.bot_dormant.schemas import (  # noqa: E402
    MarkSentRequest,
    MarkSentResponse,
    OpsActivateOneRequest,
    OpsRecycleOneRequest,
    PendingNotification,
    PendingNotificationsResponse,
)
from agentclaw.community.core.bot_dormant.internal_service import DormantInternalService  # noqa: E402
from agentclaw.community.core.bot_dormant.ops_service import DormantOpsService  # noqa: E402
from agentclaw.community.core.bot_dormant.service import DormantBotService  # noqa: E402


internal_router = APIRouter(
    prefix="/api/internal/dormant", tags=["bot-dormant-internal"]
)


@internal_router.get(
    "/pending-notifications", response_model=PendingNotificationsResponse
)
async def list_pending_notifications(
    limit: int = 200,
    dt: str | None = None,
    include_dry_run: bool = False,
    _: None = Depends(verify_dormant_internal_token),
    service: DormantInternalService = Injected(DormantInternalService),
) -> PendingNotificationsResponse:
    """Pull pending dormant notifications for the container-side sender.

    Query params:
      - limit:           max rows returned (default 200)
      - dt:              filter by 'YYYYMMDD'; defaults to all dates
      - include_dry_run: pre/dev only — set true to also receive dry_run=1
                         rows so the in-container sender can be tested
                         against the morning scan output without a real
                         recycle. Production should always omit this.
    """
    rows = service.list_pending(
        limit=limit, dt=dt, include_dry_run=include_dry_run,
    )
    return PendingNotificationsResponse(
        data=[PendingNotification(**r) for r in rows],
        total=len(rows),
    )


@internal_router.post("/mark-sent", response_model=MarkSentResponse)
async def mark_notification_sent(
    body: MarkSentRequest,
    _: None = Depends(verify_dormant_internal_token),
    service: DormantInternalService = Injected(DormantInternalService),
) -> MarkSentResponse:
    """Container side reports per-notification send result."""
    try:
        outcome = service.mark_sent(
            notify_log_id=body.id,
            success=body.success,
            error_msg=body.error_msg,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="notify_log id not found")
    return MarkSentResponse(ok=True, status=outcome)


@internal_router.post("/recycle-one")
async def recycle_one(
    body: OpsRecycleOneRequest,
    _: None = Depends(verify_dormant_internal_token),
    service: DormantOpsService = Injected(DormantOpsService),
) -> dict:
    """Ops-only helper: run exactly one bot through the dormant recycle path."""
    logger.info(
        "[dormant.ops.recycle_one] request bot_id=%s owner_id=%s dry_run=%s reason=%s",
        body.bot_id, body.owner_id, body.dry_run, body.reason,
    )
    try:
        data = service.recycle_one(
            bot_id=body.bot_id,
            owner_id=body.owner_id,
            dry_run=body.dry_run,
            reason=body.reason,
        )
        return {"ok": True, "data": data}
    except ValueError as e:
        logger.warning(
            "[dormant.ops.recycle_one] rejected bot_id=%s owner_id=%s error=%s",
            body.bot_id, body.owner_id, e,
        )
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception(
            "[dormant.ops.recycle_one] failed bot_id=%s owner_id=%s",
            body.bot_id, body.owner_id,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@internal_router.post("/activate-one")
async def activate_one(
    body: OpsActivateOneRequest,
    _: None = Depends(verify_dormant_internal_token),
    service: ActivateBotService = Injected(ActivateBotService),
) -> dict:
    """Ops-only helper: reuse the normal RECYCLED bot activation path."""
    logger.info(
        "[dormant.ops.activate_one] request bot_id=%s owner_id=%s nick_name=%s",
        body.bot_id, body.owner_id, body.nick_name,
    )
    try:
        data = service.activate(
            bot_id=body.bot_id,
            user_id=body.owner_id,
            nick_name=body.nick_name,
        )
        return {"ok": True, "data": data}
    except InvalidBotStateError as e:
        logger.warning(
            "[dormant.ops.activate_one] rejected bot_id=%s owner_id=%s error=%s",
            body.bot_id, body.owner_id, e,
        )
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception(
            "[dormant.ops.activate_one] failed bot_id=%s owner_id=%s",
            body.bot_id, body.owner_id,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---------------------------------------------------------------------------
# Internal: manual scan trigger (test/debug only — same token, no rate limit)
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402
import uuid  # noqa: E402


async def _run_in_background(
    service: DormantBotService, dry_run: bool, run_id: str,
) -> None:
    """Wrap process_run with a top-level catch so a background task crash
    never crashes the worker. All real per-row error handling already lives
    inside service.process_run — this is just the final safety net."""
    try:
        await service.process_run(dry_run=dry_run, run_id=run_id)
    except Exception:
        logger.exception(
            "[trigger-scan] background process_run crashed run_id=%s", run_id,
        )


@internal_router.post("/trigger-scan")
async def trigger_scan_now(
    dry_run: bool | None = None,
    _: None = Depends(verify_dormant_internal_token),
    service: DormantBotService = Injected(DormantBotService),
) -> dict:
    """Manually fire one dormant-bot scan (fire-and-forget).

    Returns immediately with the run_id. The actual scan runs in the
    background; poll the audit / notify_log tables (or grep log for
    ``dormant.run=<run_id>``) to track progress.

    Why fire-and-forget: a full run over 5000+ candidates takes way longer
    than Tengine's 60s gateway timeout. Returning immediately avoids 504s
    and double-fires when the user retries.

    Query params:
      - dry_run: override ac_common_config scan_policy.dry_run for this single run only.
                 Omit to use whatever the deployed config says.

    Auth: same Bearer token as the other /api/internal/dormant/* endpoints.
    """
    effective_dry_run = service.is_dry_run() if dry_run is None else dry_run
    run_id = str(uuid.uuid4())

    # Fire-and-forget: schedule the run on the event loop without awaiting.
    asyncio.create_task(
        _run_in_background(service, effective_dry_run, run_id)
    )
    logger.info(
        "[trigger-scan] dispatched background run_id=%s dry_run=%s",
        run_id, effective_dry_run,
    )

    return {
        "ok": True,
        "run_id": run_id,
        "dry_run": effective_dry_run,
        "message": (
            "scan dispatched; grep log for 'dormant.run="
            + run_id
            + "' or poll ac_bot_dormant_check_audit for progress"
        ),
    }
