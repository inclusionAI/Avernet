"""Owner-only OpenAPI operations for personal Bot dormant lifecycle."""

from __future__ import annotations

from typing import Annotated

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    ERROR_RESPONSES,
    EXAMPLE_TRACE_ID,
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    OwnerNameDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_dormant_service import (
    BotDormantActivateServiceProtocol,
    BotDormantAuditServiceProtocol,
    BotDormantRecycleOperationProtocol,
)
from agentclaw.community.di import Injected
from fastapi import APIRouter, Path, Request, Response
from starlette.concurrency import run_in_threadpool

from .schemas import BotActivateResult, BotRecycleOperation, BotRecycleResult

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["bots"],
    route_class=PublicAPIRoute,
)


@router.post(
    "/recycle",
    response_model=Envelope[BotRecycleResult],
    summary="Recycle a personal managed-cloud Bot",
    responses={
        200: {
            "description": "A synchronous recycle result, or an already-recycled idempotent result; no durable operation was started.",
            "content": {
                "application/json": {
                    "example": {
                        "code": 200000,
                        "message": "OK",
                        "request_id": EXAMPLE_TRACE_ID,
                        "data": {
                            "bot_id": "b-example",
                            "owner_id": "owner-example",
                            "status": "RECYCLED",
                            "changed": False,
                        },
                    }
                }
            },
        },
        202: {
            "model": Envelope[BotRecycleResult],
            "description": "Asynchronous admission, not completion; poll the operation handle.",
            "content": {
                "application/json": {
                    "example": {
                        "code": 202000,
                        "message": "Accepted",
                        "request_id": EXAMPLE_TRACE_ID,
                        "data": {
                            "bot_id": "b-example",
                            "owner_id": "owner-example",
                            "status": "ACTIVE",
                            "changed": True,
                            "operation_id": "0123456789abcdef0123456789abcdef",
                            "phase": "QUEUED",
                        },
                    }
                }
            },
        },
        409: {
            **ERROR_RESPONSES[409],
            "description": "Incompatible Bot state or busy lifecycle.",
        },
        503: {
            **ERROR_RESPONSES[503],
            "description": "Worker unavailable or durable admission unconfirmed.",
        },
    },
)
@envelope_errors
async def recycle_dormant_bot(
    bot_id: BotIdPath,
    request: Request,
    response: Response,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    owner_name: OwnerNameDep,
    service: BotDormantRecycleOperationProtocol = Injected(
        BotDormantRecycleOperationProtocol
    ),
    audit_service: BotDormantAuditServiceProtocol = Injected(
        BotDormantAuditServiceProtocol
    ),
) -> Envelope[BotRecycleResult]:
    """Recycle an ACTIVE personal managed-cloud Bot; no request body.

    OWNER only. Optional `entity_id` selects the Bot owner, defaulting to the
    caller; deprecated `owner_id` is still accepted. Conflicting values return
    422. Missing/inaccessible Bots return 404. Teclaw, desktop and service Bots
    are unsupported; incompatible state or a busy lifecycle returns 409.

    - Confirmed-fastdisk `aicoding` / `claude_code` Bots use HTTP 202 to admit a
      background operation. Other supported engines, and coding Bots whose probe
      is non-fastdisk or unknown, keep the original synchronous path and return
      HTTP 200 with `operation_id=null` and `phase=null`. For queued operations,
      `data.status` is the current Bot state, not completion. `changed=true`
      means new admission; a duplicate unresolved operation returns the same
      `operation_id` with `changed=false`. Poll
      `GET /openapi/v1/bots/{bot_id}/recycle-operations/{operation_id}`.
    - An already RECYCLED Bot without an unresolved operation returns HTTP 200
      with `changed=false`, without another backup, release or audit.

    Only `aicoding` / `claude_code` runs the existing fastdisk probe during
    admission and again in the worker.
    Non-coding Bots skip all backup probing, configuration and helper execution.
    Non-fastdisk coding Bots skip the helper and backup failure switch. Only
    confirmed-fastdisk coding Bots create a recycle operation; synchronous paths
    have no operation to query. Activation and lifecycle guards remain shared.
    AICoding reuses the restart backup policy. Confirmed fastdisk backup has a
    1500-second worker budget. Only enabled boolean `aicoding/fastdisk_fail_block=true`
    blocks on failure; non-fastdisk or an unsuccessful initial probe skips the
    helper. A validated `partial_success` receipt permits continuation. Blocking
    failure sets the operation to FAILED, leaves the Bot ACTIVE and its old
    binding/Passport unchanged, and does not release resources. Nonblocking
    backup failure may continue with a warning.

    The worker backs up, confirms resource release, best-effort freezes Passport,
    then sets RECYCLED and writes one audit with `source=openapi`. No recycle
    DingTalk notification is enqueued. Unconfirmed release/finalization becomes
    NEEDS_CONFIRMATION and blocks conflicting lifecycle requests; do not blindly
    resubmit destruction. Release errors after HTTP 202 are read from the operation;
    HTTP 503 means the worker is unavailable or durable admission is unconfirmed.
    """
    result = await run_in_threadpool(  # allow-run-in-threadpool: synchronous Protocol method
        service.submit,
        bot_id=bot_id,
        owner_id=owner_id,
        owner_name=owner_name,
        request_id=getattr(request.state, "request_id", "") or "",
    )
    payload = BotRecycleResult(**result)
    if payload.operation_id is None and payload.changed:
        audit_service.record_openapi_recycle(
            request_id=getattr(request.state, "request_id", "") or "",
            bot_id=payload.bot_id,
            owner_id=payload.owner_id,
        )
    if payload.operation_id is not None:
        response.status_code = 202
        return accepted(payload, request)
    return envelope(payload, request)


@router.get(
    "/recycle-operations/{operation_id}",
    response_model=Envelope[BotRecycleOperation],
    summary="Read a recycle operation",
    responses={
        200: {
            "description": "Operation read successfully, including FAILED or NEEDS_CONFIRMATION.",
            "content": {
                "application/json": {
                    "example": {
                        "code": 200000,
                        "message": "OK",
                        "request_id": EXAMPLE_TRACE_ID,
                        "data": {
                            "bot_id": "b-example",
                            "owner_id": "owner-example",
                            "operation_id": "0123456789abcdef0123456789abcdef",
                            "bot_status": "ACTIVE",
                            "phase": "FAILED",
                            "error_code": "BACKUP_FAILED",
                            "message": "Backup failed; resources were not released.",
                            "warning": None,
                        },
                    }
                }
            },
        }
    },
)
@envelope_errors
async def get_recycle_operation(
    bot_id: BotIdPath,
    operation_id: Annotated[
        str, Path(description="Recycle operation handle returned by POST /recycle.")
    ],
    request: Request,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    service: BotDormantRecycleOperationProtocol = Injected(
        BotDormantRecycleOperationProtocol
    ),
) -> Envelope[BotRecycleOperation]:
    """Read-only query of the current/latest personal Bot recycle operation.

    HTTP 200 means the read succeeded, even for phase FAILED or
    NEEDS_CONFIRMATION. `bot_status` is independent of the operation phase:
    QUEUED, BACKING_UP, RELEASING, SUCCEEDED, FAILED or NEEDS_CONFIRMATION.
    Blocking BACKUP_FAILED / BACKUP_TIMEOUT leaves the Bot ACTIVE and does not
    release resources. RELEASE_UNCONFIRMED / FINALIZATION_UNCONFIRMED requires
    operator verification, not an automatic destructive retry. `warning` may
    contain BACKUP_FAILED_CONTINUE or PASSPORT_FREEZE_FAILED (code/message).

    This GET never advances, retries or repairs work. Only the current/latest
    handle is retained; an older replaced handle, mismatched scope, unsupported
    Bot type or missing/inaccessible Bot returns 404. Uses the same OWNER check,
    optional `entity_id` and deprecated `owner_id` alias as POST /recycle;
    conflicting owner parameters return 422. This is separate from Bot /status.
    """
    result = await run_in_threadpool(  # allow-run-in-threadpool: synchronous Protocol method
        service.get_operation,
        bot_id=bot_id,
        owner_id=owner_id,
        operation_id=operation_id,
    )
    return envelope(BotRecycleOperation(**result), request)


@router.post(
    "/activate",
    response_model=Envelope[BotActivateResult],
    summary="Reactivate a recycled personal managed-cloud Bot",
    responses={
        200: {
            "description": "Bot is already ACTIVE; no new activation was started.",
            "content": {
                "application/json": {
                    "example": {
                        "code": 200000,
                        "message": "OK",
                        "request_id": EXAMPLE_TRACE_ID,
                        "data": {
                            "bot_id": "b-example",
                            "owner_id": "owner-example",
                            "status": "ACTIVE",
                            "changed": False,
                        },
                    }
                }
            },
        },
        202: {
            "model": Envelope[BotActivateResult],
            "description": "REACTIVATING; observe readiness through GET /openapi/v1/bots/{bot_id}/status.",
            "content": {
                "application/json": {
                    "example": {
                        "code": 202000,
                        "message": "Accepted",
                        "request_id": EXAMPLE_TRACE_ID,
                        "data": {
                            "bot_id": "b-example",
                            "owner_id": "owner-example",
                            "status": "REACTIVATING",
                            "changed": True,
                        },
                    }
                }
            },
        },
        409: {
            **ERROR_RESPONSES[409],
            "description": "Recycle is unresolved or Bot lifecycle state conflicts.",
        },
    },
)
@envelope_errors
async def activate_dormant_bot(
    bot_id: BotIdPath,
    request: Request,
    response: Response,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    owner_name: OwnerNameDep,
    service: BotDormantActivateServiceProtocol = Injected(
        BotDormantActivateServiceProtocol
    ),
) -> Envelope[BotActivateResult]:
    """Start or observe reactivation of a recycled personal managed-cloud Bot.

    No request body. OWNER only; `entity_id` is optional and defaults to the
    caller. Deprecated `owner_id` remains accepted; conflicting values return
    422. Missing/inaccessible Bots return 404. Teclaw, desktop and service Bots
    are unsupported.

    HTTP 202 / REACTIVATING means activation is pending: `changed=true` for a
    new activation and `changed=false` for an already pending one. Already ACTIVE
    returns HTTP 200 / `changed=false`. A running recycle or NEEDS_CONFIRMATION
    returns 409 before the ACTIVE idempotency check; other incompatible states
    also return 409.

    Poll `GET /openapi/v1/bots/{bot_id}/status` for runtime readiness. Existing
    provisioning restores backup data; no separate activation operation handle
    is introduced, and the latest recycle result is retained.
    """
    result = await run_in_threadpool(  # allow-run-in-threadpool: synchronous Protocol method
        service.activate,
        bot_id=bot_id,
        owner_id=owner_id,
        owner_name=owner_name,
    )
    payload = BotActivateResult(**result.__dict__)
    if result.status == "REACTIVATING":
        response.status_code = 202
        return accepted(payload, request)
    return envelope(payload, request)
