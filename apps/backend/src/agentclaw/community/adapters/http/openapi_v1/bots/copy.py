"""POST /openapi/v1/bots/copy — copy an openclaw bot to a new dst bot.

Step1a: validate source (engine=openclaw + copyable lifecycle + edit perm),
write the `ac_bot_copy{INITIAL}` operation ledger row, apply for Passport, and
hand off to create_job with `state_ref=copy_state(operation_id)` in the payload
spec. create_job runs the reused manifest-creation flow (apply_pre_container →
provision → apply_on_container); CopyCompiler (Stage B Task 6) consumes
`state_ref` at apply time to capture source's live config + screens + NAS files.

**Route mounting deferred to Stage B Task 7** (DI wiring): the router + handler
are defined here but not yet included in `openapi_v1/__init__.py`, so the app
does not boot with unbound `BotCopyRepositoryProtocol` in the interim. The
handler-unit tests exercise `submit_copy` directly by passing deps by keyword.
"""

from __future__ import annotations

import random
import string
from datetime import datetime
from enum import StrEnum
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi_injector import Injected
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.principal import (
    OwnerNameDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import envelope_errors
from agentclaw.community.core.bot_config_manifest.creation import ManifestCreationSeam
from agentclaw.community.core.bot_copy.repository.protocol import (
    BotCopyRepositoryProtocol,
)
from agentclaw.community.core.bot_copy.state_ref import copy_state
from agentclaw.community.core.bot_copy.status_derive import compute_copy_status
from agentclaw.community.core.bot_copy.types import BotCopyRow, BotCopyStatus
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import generate_bot_id
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

#: Copy supports openclaw bots only (PRD v1); teclaw's container is composed by
#: the artifact at provision, a different mechanism from copy's pre/post delivery.
_COPYABLE_ENGINES = {"openclaw"}

#: Source lifecycle states that block copy (PRD precondition: deploying /
#: failed / unknown). Active / prestable / offline / prestable-pending copy OK.
_NON_COPYABLE_LIFECYCLE = {"DEPLOYING", "FAILED", "UNKNOWN"}

#: Operator retry cas_retry throttle window (seconds). FE shows the retry
#: button for every copy_state except CONVERGED and may click freely; this CAS
#: is the spam guard. create_job's UNIQUE active_idempotency_key handles dedup
#: if a task is still live, so retry is safe to fire at any non-throttled time.
_RETRY_THROTTLE_SECONDS = 60


class BotCopyRequest(BaseModel):
    """POST /openapi/v1/bots/copy body."""

    model_config = {"extra": "forbid"}

    source_bot_id: str = Field(..., description="源 bot 的 bot_id(仅 openclaw)")
    bot_name: str = Field(..., description="dst bot 显示名")
    bot_desc: Optional[str] = Field(default=None, description="dst bot 描述")
    request_detail: Optional[dict[str, Any]] = Field(
        default=None,
        description='JSON: {"excludes": [...], ...未来 copy 参数}',
    )


class BotCopyResponse(BaseModel):
    """POST /copy response — success (OSS sync ISSUED) or auth_required (internal)."""

    type: str = Field(
        ..., description='"success" (OSS NoOp sync ISSUED) | "auth_required" (internal license PENDING)'
    )
    operation_id: str = Field(..., description="copy 操作幂等 id + FE correlation id")
    bot_id: str = Field(..., description="新建 dst bot 的 bot_id")
    iframe_url: Optional[str] = Field(default=None)
    redirect_url: Optional[str] = Field(default=None)


router = APIRouter(prefix="/openapi/v1/bots", tags=["bots"])


def _generate_operation_id() -> str:
    """A globally-unique operation id (mirrors `generate_bot_id`'s time+random shape)."""
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"copy_{date_part}_{random_part}"


def submit_copy(
    *,
    body: BotCopyRequest,
    owner_id: str,
    owner_name: str,
    bot_repo: BotRepository,
    bot_copy_repo: BotCopyRepositoryProtocol,
    manifest_seam: ManifestCreationSeam,
    passport_plugin: PassportPlugin,
    space_context: BusinessSpaceContextProtocol,
) -> BotCopyResponse:
    """Validate source + write ledger + apply Passport + hand off to create_job.

    Raises ``HTTPException`` (409 / 403 / 500) for validation/license failures;
    on any failure before ``start_job`` the ledger write (if it landed) is left
    for the operator-retry path to reconcile — the handler does not compensate,
    matching the inline `create_bot` surface.
    """
    del owner_name  # retained for the route handler's signature symmetry
    source = bot_repo.get_by_id(body.source_bot_id)
    if source is None:
        raise HTTPException(
            status_code=409, detail=f"source bot {body.source_bot_id} not found"
        )
    engine = source.get("active_engine") or source.get("engine_type")
    if engine not in _COPYABLE_ENGINES:
        raise HTTPException(
            status_code=409,
            detail=f"copy supports openclaw bots only; source engine={engine}",
        )
    if source.get("status") in _NON_COPYABLE_LIFECYCLE:
        raise HTTPException(
            status_code=409,
            detail=f"source bot status {source.get('status')} not copyable",
        )
    actions = source.get("actions") or []
    if source.get("owner_id") != owner_id and "edit" not in actions:
        raise HTTPException(
            status_code=403, detail="no edit permission on source bot"
        )

    operation_id = _generate_operation_id()
    dst_bot_id = generate_bot_id(owner_id, bot_repo)

    current_space = space_context.resolve_current(owner_id=owner_id, header_space_id=None)
    space_id = getattr(current_space, "numeric_id", None)

    bot_copy_repo.insert(
        BotCopyRow(
            operation_id=operation_id,
            source_bot_id=source["bot_id"],
            source_owner_id=source.get("owner_id") or "",
            dst_bot_id=dst_bot_id,
            dst_owner_id=owner_id,
            operator_id=owner_id,
            status=BotCopyStatus.INITIAL,
            request_detail=body.request_detail,
            env=get_current_env(),
            avernet_tenant=get_current_avernet_tenant(),
        )
    )

    # The payload spec create_job freezes into its task payload. `state_ref`
    # routes the apply to CopyCompiler (Stage B); `source_bot_id` +
    # `request_detail.excludes` are what CopyCompiler reads at compile time.
    payload_spec: dict[str, Any] = {
        "entity_id": owner_id,
        "entity_type": "staff",
        "engine_type": "openclaw",
        "bot_type": source.get("bot_type") or "personal",
        "bot_name": body.bot_name,
        "bot_desc": body.bot_desc,
        "space_id": space_id,
        "state_ref": copy_state(operation_id).to_payload(),
        "request_detail": body.request_detail,
        "source_bot_id": source["bot_id"],
    }

    passport_result = passport_plugin.apply_agent_passport(
        bot_id=dst_bot_id,
        owner_workno=owner_id,
        mcp_codes=[],
        bot_name=body.bot_name,
        bot_desc=body.bot_desc,
        engine_type="openclaw",
    )
    iframe_url = (passport_result or {}).get("iframe_url")
    redirect_url = (passport_result or {}).get("redirect_url")
    token = (passport_result or {}).get("token")
    if not token and not iframe_url and not redirect_url:
        raise HTTPException(
            status_code=500,
            detail="passport returned no token and no authorization url",
        )

    manifest_seam.start_job(
        bot_id=dst_bot_id,
        entity_id=owner_id,
        user_id=owner_id,
        document_owner=owner_id,
        spec=payload_spec,
        iframe_url=iframe_url,
        redirect_url=redirect_url,
    )

    if token:
        return BotCopyResponse(
            type="success", operation_id=operation_id, bot_id=dst_bot_id
        )
    return BotCopyResponse(
        type="auth_required",
        operation_id=operation_id,
        bot_id=dst_bot_id,
        iframe_url=iframe_url,
        redirect_url=redirect_url,
    )


@router.post(
    "/copy",
    status_code=200,
    response_model=Envelope[BotCopyResponse],
    responses={
        202: {
            "description": "Needs user authorization (internal license gate)",
        },
        403: {"description": "No edit permission on source"},
        409: {"description": "Source not copyable (engine / lifecycle / not found)"},
    },
)
@envelope_errors
async def copy_bot(
    body: BotCopyRequest,
    request: Request,
    owner_id: UserIdDep,
    owner_name: OwnerNameDep,
    bot_repo: BotRepository = Injected(BotRepository),
    bot_copy_repo: BotCopyRepositoryProtocol = Injected(BotCopyRepositoryProtocol),
    manifest_seam: ManifestCreationSeam = Injected(ManifestCreationSeam),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    space_context: BusinessSpaceContextProtocol = Injected(BusinessSpaceContextProtocol),
) -> Envelope[BotCopyResponse]:
    """Copy an openclaw bot to a new dst bot (v1).

    200 `{type:"success", operation_id, bot_id}` on OSS sync ISSUED; 202-style
    `{type:"auth_required", iframe_url, operation_id, bot_id}` on internal license
    PENDING (have the user authorize at `iframe_url`, then poll
    `/openapi/v1/bots/{dst_bot_id}` `copy_state`). 409 for teclaw source or
    non-copyable lifecycle; 403 without edit permission.
    """
    result = submit_copy(
        body=body,
        owner_id=owner_id,
        owner_name=owner_name,
        bot_repo=bot_repo,
        bot_copy_repo=bot_copy_repo,
        manifest_seam=manifest_seam,
        passport_plugin=passport_plugin,
        space_context=space_context,
    )
    return Envelope(data=result)


__all__ = [
    "BotCopyRequest",
    "BotCopyResponse",
    "RetryResponse",
    "RetryResult",
    "retry_copy",
    "router",
    "submit_copy",
]


class RetryResult(StrEnum):
    """The retry endpoint's outcome."""

    RE_ENQUEUED = "re_enqueued"
    STILL_RUNNING = "still_running"
    TERMINAL = "terminal"


class RetryResponse(BaseModel):
    """The retry endpoint's body: the outcome + the operation_id for FE context."""

    operation_id: str = Field(..., description="the copy operation retried")
    result: RetryResult = Field(..., description="the retry outcome")


def retry_copy(
    *,
    operation_id: str,
    operator_id: str,
    bot_copy_repo: BotCopyRepositoryProtocol,
    apply_repo: BotConfigManifestApplyRepositoryProtocol,
    bot_repo: BotRepository,
    manifest_seam: ManifestCreationSeam,
    now: datetime,
) -> RetryResult:
    """Operator-triggered retry of one copy operation.

    FE shows the retry button for every ``copy_state`` except ``CONVERGED`` and
    may click freely. Three guards make that safe:

    1. ``CONVERGED`` → ``TERMINAL`` (no-op).
    2. ``cas_retry`` CAS (60s) → ``STILL_RUNNING`` on a repeat click inside the
       window (the spam guard).
    3. ``create_job``'s ``active_idempotency_key`` UNIQUE → a re-handoff while a
       task is still live dedupes at the queue, so a non-throttled retry that
       lands during an active task is a no-op rather than a second task.

    Re-handoff goes through ``manifest_seam.start_job`` (the same seam first
    submission used), with the same ``state_ref=copy_state(op_id)`` so CopyCompiler
    (Stage B) re-captures source's live config. No authorization window: the bot
    was authorized at first submission (license already ISSUED), and retry is
    FE-manual — ``start_job``'s configured window is moot because the create_job
    task's first Passport poll sees ISSUED and proceeds.
    """
    del operator_id  # retained for audit/route signature; not yet written to ledger
    row = bot_copy_repo.get_by_operation_id(
        env=get_current_env(), operation_id=operation_id
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"copy operation {operation_id} not found"
        )
    env = row.env

    applies = apply_repo.recent(
        env=env, entity_id=row.dst_owner_id, bot_id=row.dst_bot_id, limit=10
    )
    bot = bot_repo.get_by_id(row.dst_bot_id)
    bot_status = (bot or {}).get("status")
    derived = compute_copy_status(
        copy_row=row, dst_apply_records=applies, dst_bot_status=bot_status
    )
    if derived is BotCopyStatus.CONVERGED:
        return RetryResult.TERMINAL

    if not bot_copy_repo.cas_retry(
        env=env, operation_id=operation_id, now=now, throttle_seconds=_RETRY_THROTTLE_SECONDS
    ):
        return RetryResult.STILL_RUNNING

    payload_spec: dict[str, Any] = {
        "entity_id": row.dst_owner_id,
        "entity_type": "staff",
        "engine_type": "openclaw",
        "bot_type": "personal",
        "bot_name": None,
        "bot_desc": None,
        "space_id": None,
        "state_ref": copy_state(operation_id).to_payload(),
        "request_detail": row.request_detail,
        "source_bot_id": row.source_bot_id,
    }
    manifest_seam.start_job(
        bot_id=row.dst_bot_id,
        entity_id=row.dst_owner_id,
        user_id=row.operator_id,
        document_owner=row.operator_id,
        spec=payload_spec,
        iframe_url=None,
        redirect_url=None,
    )
    return RetryResult.RE_ENQUEUED


@router.post(
    "/copy/{operation_id}/retry",
    status_code=200,
    response_model=Envelope[RetryResponse],
    responses={
        404: {"description": "Unknown operation_id"},
    },
)
@envelope_errors
async def retry_copy_bot(
    operation_id: str,
    request: Request,
    owner_id: UserIdDep,
    bot_copy_repo: BotCopyRepositoryProtocol = Injected(BotCopyRepositoryProtocol),
    apply_repo: BotConfigManifestApplyRepositoryProtocol = Injected(
        BotConfigManifestApplyRepositoryProtocol
    ),
    bot_repo: BotRepository = Injected(BotRepository),
    manifest_seam: ManifestCreationSeam = Injected(ManifestCreationSeam),
) -> Envelope[RetryResponse]:
    """Re-hand-off a copy operation to create_job (operator-triggered retry).

    FE shows this for every ``copy_state`` except ``CONVERGED``. Three guards
    (CONVERGED terminal / 60s CAS throttle / create_job idempotency UNIQUE) make
    it safe to click freely. Returns the outcome + the operation_id.
    """
    del request  # envelope_errors requires a Request param; unused on the success path
    result = retry_copy(
        operation_id=operation_id,
        operator_id=owner_id,
        bot_copy_repo=bot_copy_repo,
        apply_repo=apply_repo,
        bot_repo=bot_repo,
        manifest_seam=manifest_seam,
        now=datetime.now(),
    )
    return Envelope(data=RetryResponse(operation_id=operation_id, result=result))
