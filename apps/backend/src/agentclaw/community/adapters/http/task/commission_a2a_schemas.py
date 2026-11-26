"""Bounded A2A 0.3.0 JSON-RPC message/send -> ACCS Query/Open/Work.

This is the real HTTP wire, not the P1 A2aMessage mock projection. An A2A
``user`` role denotes the sending client, which here is an authenticated Bot.
No remote A2A Task/session identifier becomes a Collet identifier or credential.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.gateway_principal import GatewayBot
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    IdentityRef,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.envelope import (
    AccsOperationEnvelope,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    TASK_PROGRESS_DETAIL_FIELDS,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskAdvanceCommand,
    TaskExecutionReportCommand,
)

ISSUER = "avernet"
A2A_VERSION = "0.3.0"
RPC_PATH = "/a2a/task-commission"
_ACTIONS = {
    "start": "start_task",
    "progress": "report_progress",
    "submit": "submit_task",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DataPart(_StrictModel):
    kind: Literal["data"]
    data: dict


class ClientMessage(_StrictModel):
    kind: Literal["message"]
    messageId: str = Field(min_length=1, max_length=256)  # noqa: N815
    role: Literal["user"]
    parts: list[DataPart] = Field(min_length=1, max_length=1)


class SendParams(_StrictModel):
    message: ClientMessage


class RpcRequest(_StrictModel):
    jsonrpc: Literal["2.0"]
    id: str | int  # Required: this bounded endpoint does not admit notifications.
    method: str
    params: dict


class ReportPayload(_StrictModel):
    note: str | None = Field(default=None, max_length=16000)
    percent: int | None = Field(default=None, ge=0, le=100)
    artifacts: list[str] = Field(default_factory=list, max_length=100)
    progress: str | None = Field(default=None, max_length=16000)
    error_msg: str | None = Field(default=None, max_length=16000)
    reason: str | None = Field(default=None, max_length=16000)
    task_id: str | None = Field(default=None, min_length=1, max_length=256)


def _timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContextCollabValidationError("invalid operation timestamp") from exc
    if result.tzinfo is None:
        raise ContextCollabValidationError("operation timestamps require timezone")
    return result


def authenticated_operation_deadline(
    operation: AccsOperationEnvelope, bot: GatewayBot
) -> datetime | None:
    """Common signed identity/version/extension/deadline validation for this endpoint."""
    ctx = operation.context
    if ctx.caller != IdentityRef("AGENT", bot.bot_uuid, ISSUER):
        raise Forbidden("operation caller does not match authenticated Bot")
    if ctx.accountable_principal_ref != IdentityRef("PRINCIPAL", bot.owner_id, ISSUER):
        raise Forbidden("accountable principal does not match authenticated Bot owner")
    if ctx.protocol_versions != {"accs": "0.2", "a2a": A2A_VERSION}:
        raise ContextCollabValidationError("unsupported protocol versions")
    if (
        ctx.idempotency_key is not None
        or ctx.delegation_chain_refs
        or ctx.extensions
        or operation.input_refs
    ):
        raise ContextCollabValidationError(
            "unsupported operation extension or idempotency contract"
        )
    # requested_at is attribution, not authority. Work additionally checks the
    # deadline before its SQL commit; Query/Open have an ingress deadline only.
    _timestamp(ctx.requested_at)
    deadline = _timestamp(ctx.deadline) if ctx.deadline is not None else None
    if deadline is not None and deadline <= datetime.now(timezone.utc):
        raise ContextCollabValidationError("operation deadline has expired")
    return deadline


def report_command(
    operation: AccsOperationEnvelope, bot: GatewayBot
) -> TaskExecutionReportCommand:
    """Translate only supported, authenticated context; never trust caller IDs."""
    deadline = authenticated_operation_deadline(operation, bot)
    ctx = operation.context
    if (
        operation.layer != "L4"
        or operation.operation_family != "work"
        or operation.mode != "COMMAND"
        or operation.action not in _ACTIONS
    ):
        raise ContextCollabValidationError("unsupported ACCS executor operation")
    target = operation.target_ref
    if (
        target is None
        or target.object_type != "Collet"
        or target.issuer != ISSUER
        or target.version is not None
        or ctx.collet_ref is None
        or ctx.collet_ref.context_type != "COLLET"
        or ctx.collet_ref.context_id != target.object_id
    ):
        raise ContextCollabValidationError("report target and Collet context disagree")
    binding = ctx.method_binding_ref
    profile = ctx.product_profile_ref
    if (
        binding is None
        or binding.object_type != "MethodBinding"
        or binding.issuer != ISSUER
        or binding.version is not None
        or profile is None
        or profile.object_type != "ProductProfile"
        or profile.object_id != "task_commission"
        or profile.issuer != ISSUER
        or profile.version is None
        or not profile.version.isascii()
        or not profile.version.isdecimal()
        or profile.version.startswith("0")
    ):
        raise ContextCollabValidationError(
            "report requires a supported binding/product context"
        )
    try:
        profile_version = int(profile.version)
    except ValueError as error:
        # Python also rejects decimal strings beyond its integer conversion limit.
        raise ContextCollabValidationError("invalid product profile version") from error
    if type(operation.expected_revision) is not int or operation.expected_revision < 1:
        raise ContextCollabValidationError("report requires positive expected_revision")
    payload = ReportPayload.model_validate(operation.payload)
    fields = payload.model_fields_set
    allowed = {
        "start": {"note"},
        "progress": {"note", "percent", *TASK_PROGRESS_DETAIL_FIELDS},
        "submit": {"note", "artifacts"},
    }[operation.action]
    if not fields <= allowed or any(not item.strip() for item in payload.artifacts):
        raise ContextCollabValidationError("report payload does not match its action")
    return TaskExecutionReportCommand(
        action=_ACTIONS[operation.action],
        task=TaskAdvanceCommand(
            collab_context_id=target.object_id,
            note=payload.note,
            percent=payload.percent,
            artifacts=payload.artifacts,
            progress=payload.progress,
            error_msg=payload.error_msg,
            reason=payload.reason,
            task_id=payload.task_id,
        ),
        expected_version=operation.expected_revision,
        product_profile_ref=VersionedProfileRef(profile.object_id, profile_version),
        method_binding_ref=binding.object_id,
        deadline=deadline,
    )
