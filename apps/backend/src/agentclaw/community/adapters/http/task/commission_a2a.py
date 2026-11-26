"""Bot-authenticated A2A ingress for commission discovery, application and Work."""

from __future__ import annotations

import json
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from agentclaw.community.adapters.http.task.commission_a2a_auth import (
    require_commission_bot,
)
from agentclaw.community.adapters.http.task.commission_a2a_schemas import (
    ISSUER,
    RPC_PATH,
    RpcRequest,
    SendParams,
    report_command,
)
from agentclaw.community.adapters.http.task.translator_commission_open_a2a import (
    execute_open_operation,
)
from agentclaw.community.api.task.task_commission_service import (
    TaskCommissionServiceProtocol,
)
from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.gateway_principal import GatewayBot
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    AccsError,
    ObjectRef,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.envelope import (
    AccsOperationEnvelope,
    AccsOperationResult,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.di import Injected
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

logger = logging.getLogger(__name__)
router = APIRouter(tags=["task-commission-a2a"])
_MAX_BODY_BYTES = 65536


def _rpc_error(request_id: str | int | None, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
    )


def _execute_commission_operation(
    operation: AccsOperationEnvelope,
    bot: GatewayBot,
    service: TaskCommissionServiceProtocol,
) -> tuple[AccsOperationResult, dict | None]:
    op_ref = ObjectRef("Operation", operation.operation_id, None, ISSUER)
    try:
        # SQL discovery and Work use the signed Bot tenant on this worker,
        # never a body/query tenant. L5 retains its current single-tenant axes.
        with avernet_tenant_scope(bot.tenant):
            if operation.operation_family in {"query", "open"}:
                return execute_open_operation(operation, bot, service)
            command = report_command(operation, bot)
            result = service.report_execution(
                command,
                ActorContext(PrincipalRef("bot", bot.bot_uuid), source="a2a"),
            )
        return AccsOperationResult(
            op_ref,
            "SUCCEEDED",
            (
                ObjectRef(
                    "Collet",
                    result.collab_context_id,
                    str(result.context_version),
                    ISSUER,
                ),
            ),
            (ObjectRef("Event", result.event_id, None, ISSUER),),
            None,
        ), None
    except Forbidden:
        category, message = "AUTHORIZATION", "Bot is not authorized for this operation"
    except ContextCollabNotFoundError:
        category, message = "NOT_FOUND", "Task not found"
    except ContextCollabVersionConflict:
        category, message = "CONFLICT", "Stale revision; reconcile before retrying"
    except (ContextCollabValidationError, ValidationError):
        category, message = "VALIDATION", "Invalid operation, actor or task state"
    except Exception:
        logger.exception(
            "Commission operation failed; operation_id=%r", operation.operation_id[:256]
        )
        category, message = (
            "INTERNAL",
            "Operation failed; reconcile task state before retrying",
        )
    return AccsOperationResult(
        op_ref,
        "FAILED" if category == "INTERNAL" else "REJECTED",
        (),
        (),
        AccsError(
            code=f"COMMISSION_{category}",
            message=message,
            retryable=False,
            category=category,
            trace_id=operation.context.trace_id,
            object_ref=None,
            details=None,
        ),
    ), None


@router.post(
    RPC_PATH,
    response_class=JSONResponse,
    openapi_extra={"x-avernet-security": {"bot": "required"}},
)
async def receive_executor_report(
    request: Request,
    bot: GatewayBot = Depends(require_commission_bot),
    service: TaskCommissionServiceProtocol = Injected(TaskCommissionServiceProtocol),  # noqa: B008
) -> JSONResponse:
    """A2A 0.3.0 message/send only; no streaming, Tasks or session resumption."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
    try:
        raw = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return _rpc_error(None, -32700, "Parse error")
    rpc_id = raw.get("id") if isinstance(raw, dict) else None
    if type(rpc_id) not in (str, int):
        rpc_id = None
    try:
        rpc = RpcRequest.model_validate(raw)
    except ValidationError:
        return _rpc_error(rpc_id, -32600, "Invalid Request")
    if rpc.method != "message/send":
        return _rpc_error(rpc_id, -32601, "Method not found")
    try:
        params = SendParams.model_validate(rpc.params)
        operation = AccsOperationEnvelope.from_dict(params.message.parts[0].data)
    except (ValidationError, ContextCollabValidationError, TypeError, AttributeError):
        return _rpc_error(rpc_id, -32602, "Invalid params")
    result, data = await run_in_threadpool(
        _execute_commission_operation, operation, bot, service
    )
    parts = [{"kind": "data", "data": result.to_dict()}]
    if data is not None:
        parts.append({"kind": "data", "data": data})
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": rpc.id,
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": str(uuid4()),
                "parts": parts,
            },
        }
    )
