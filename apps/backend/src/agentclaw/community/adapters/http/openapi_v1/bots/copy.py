"""POST /openapi/v1/bots/copy — copy an openclaw bot to a new dst bot (Route B).

Route B (manifest-reuse): capture source's live config, **serialize it into a
manifest config document**, persist it under the dst bot's key, and hand off to
the **reused manifest-creation** flow (``manifest_seam`` → ``create_job`` →
ManifestCompiler → apply engine). No ``CopyCompiler``, no ``state_ref`` — the
dst becomes a manifest-managed bot, and ``create_job`` / ``creation.py`` /
``apply_engine`` core are untouched (zero regression risk).

**v1 scope (script only).** ``identity`` / ``resources`` are file-based and
deferred to a later **sync** stage (``BotFileSyncPort``); ``skills`` / ``mcp``
are deferred to a later **能力集** stage. So the serialized manifest carries
only ``script`` (read from source via ``BotStartupScriptServiceProtocol``).
When sync + 能力集 stages land, the serializer emits the extra sections.

**Route mounting deferred to Task 7** (DI wiring): the router + handler are
defined here but not yet included in ``openapi_v1/__init__.py``. Handler-unit
tests pass deps by keyword.
"""

from __future__ import annotations

import random
import string
from datetime import datetime
from enum import StrEnum
from typing import Any, Optional

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi_injector import Injected
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
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
from agentclaw.community.core.bot_copy.status_derive import compute_copy_status
from agentclaw.community.core.bot_copy.types import BotCopyRow, BotCopyStatus
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

#: Copy supports openclaw bots only (PRD v1); teclaw's container is composed by
#: the artifact at provision, a different mechanism from manifest-creation's
#: pre/post-container delivery.
_COPYABLE_ENGINES = {"openclaw"}

#: Source lifecycle states that block copy (PRD precondition: deploying /
#: failed / unknown). Active / prestable / offline / prestable-pending copy OK.
_NON_COPYABLE_LIFECYCLE = {"DEPLOYING", "FAILED", "UNKNOWN"}

#: Operator retry cas_retry throttle window (seconds). FE shows the retry
#: button for every copy_state except CONVERGED and may click freely; this CAS
#: is the spam guard. create_job's UNIQUE active_idempotency_key dedupes if a
#: task is still live.
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
        ...,
        description='"success" (OSS NoOp sync ISSUED) | "auth_required" (internal license PENDING)',
    )
    operation_id: str = Field(..., description="copy 操作幂等 id + FE correlation id")
    bot_id: str = Field(..., description="新建 dst bot 的 bot_id")
    iframe_url: Optional[str] = Field(default=None)
    redirect_url: Optional[str] = Field(default=None)


router = APIRouter(prefix="/openapi/v1/bots", tags=["bots"], route_class=PublicAPIRoute)


def _generate_operation_id() -> str:
    """A globally-unique operation id (mirrors `generate_bot_id`'s time+random shape)."""
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"copy_{date_part}_{random_part}"


def _build_manifest_document(script_body: str) -> str:
    """Serialize source's script into a manifest config document (v1: script only).

    The document is what ``manifest_seam.persist`` stores under the dst bot's key
    and what ManifestCompiler compiles. ``identity`` / ``resources`` / ``skills``
    / ``mcp`` are deferred (sync + 能力集 stages); when they land this function
    emits the extra sections.
    """
    return yaml.safe_dump(
        {"schema_version": 1, "script": {"body": script_body}},
        sort_keys=False,
        allow_unicode=True,
    )


def submit_copy(
    *,
    body: BotCopyRequest,
    owner_id: str,
    owner_name: str,
    bot_repo: BotRepository,
    bot_copy_repo: BotCopyRepositoryProtocol,
    bot_script_service: BotStartupScriptServiceProtocol,
    manifest_seam: ManifestCreationSeam,
    passport_plugin: PassportPlugin,
    space_context: BusinessSpaceContextProtocol,
) -> BotCopyResponse:
    """Validate source + serialize manifest + persist + apply Passport + hand off.

    Route B: the manifest document is built from source's live config (v1:
    script only), persisted under the dst bot's key, and the reused
    manifest-creation flow (``manifest_seam.start_job`` → ``create_job``) drives
    apply + provision. ``creation.py`` / ``create_job`` are untouched.
    """
    del owner_name
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
        raise HTTPException(status_code=403, detail="no edit permission on source bot")

    operation_id = _generate_operation_id()
    # Lazy import: bot_service sits in a circular edge with workspace_service
    # that only post-top-import ordering resolves; importing at module top can
    # trip it when this module is imported lazily inside openapi_v1's router
    # composition.
    from agentclaw.community.core.bot_management.services.bot_service import (
        generate_bot_id,
    )

    dst_bot_id = generate_bot_id(owner_id, bot_repo)

    current_space = space_context.resolve_current(owner_id=owner_id, header_space_id=None)
    space_id = getattr(current_space, "numeric_id", None)

    # Route B: serialize source's config into a manifest document (v1: script only).
    source_owner_id = source.get("owner_id") or owner_id
    source_entity_id = source.get("entity_id") or source_owner_id
    script_body = bot_script_service.get_body(
        entity_id=source_entity_id, bot_id=source["bot_id"]
    )
    manifest_document = _build_manifest_document(script_body)

    # Validate the document before anything is spent (manifest-creation's rule:
    # an invalid doc never reaches Passport). Raises ManifestValidationError.
    manifest_seam.preflight(
        document=manifest_document,
        engine_type="openclaw",
        bot_type=source.get("bot_type") or "personal",
    )
    # Persist under the dst bot's key — ManifestCompiler reads this at apply time.
    manifest_seam.persist(
        spec_entity_id=owner_id,
        bot_id=dst_bot_id,
        document=manifest_document,
        modifier=owner_id,
        engine_type="openclaw",
        bot_type=source.get("bot_type") or "personal",
    )

    # Operation ledger: FE correlation id + source/dst link + excludes. Written
    # after the manifest is stored so a persist failure leaves no orphan row.
    bot_copy_repo.insert(
        BotCopyRow(
            operation_id=operation_id,
            source_bot_id=source["bot_id"],
            source_owner_id=source_owner_id,
            dst_bot_id=dst_bot_id,
            dst_owner_id=owner_id,
            operator_id=owner_id,
            status=BotCopyStatus.INITIAL,
            request_detail=body.request_detail,
            env=get_current_env(),
            avernet_tenant=get_current_avernet_tenant(),
        )
    )

    payload_spec: dict[str, Any] = {
        "entity_id": owner_id,
        "entity_type": "staff",
        "engine_type": "openclaw",
        "bot_type": source.get("bot_type") or "personal",
        "bot_name": body.bot_name,
        "bot_desc": body.bot_desc,
        "space_id": space_id,
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
    """Operator-triggered retry of one copy operation (Route B re-hand-off).

    The manifest document was persisted at first submission under the dst bot's
    key; retry re-hands-off to ``create_job`` via ``manifest_seam.start_job``
    with the same payload spec. ``create_job`` is idempotent on ``bot_id``
    (``complete_bot_authorization`` returns the existing row; ``apply_pre_container``
    checks existing apply records; ``active_idempotency_key`` UNIQUE dedupes the
    task). FE shows retry for every ``copy_state`` except CONVERGED.
    """
    del operator_id
    row = bot_copy_repo.get_by_operation_id(
        env=get_current_env(), operation_id=operation_id
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"copy operation {operation_id} not found"
        )

    applies = apply_repo.recent(
        env=row.env, entity_id=row.dst_owner_id, bot_id=row.dst_bot_id, limit=10
    )
    bot = bot_repo.get_by_id(row.dst_bot_id)
    bot_status = (bot or {}).get("status")
    derived = compute_copy_status(
        copy_row=row, dst_apply_records=applies, dst_bot_status=bot_status
    )
    if derived is BotCopyStatus.CONVERGED:
        return RetryResult.TERMINAL

    if not bot_copy_repo.cas_retry(
        env=row.env,
        operation_id=operation_id,
        now=now,
        throttle_seconds=_RETRY_THROTTLE_SECONDS,
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
    "/copy",
    status_code=200,
    response_model=Envelope[BotCopyResponse],
    responses={
        202: {"description": "Needs user authorization (internal license gate)"},
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
    bot_script_service: BotStartupScriptServiceProtocol = Injected(
        BotStartupScriptServiceProtocol
    ),
    manifest_seam: ManifestCreationSeam = Injected(ManifestCreationSeam),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    space_context: BusinessSpaceContextProtocol = Injected(BusinessSpaceContextProtocol),
) -> Envelope[BotCopyResponse]:
    """Copy an openclaw bot to a new dst bot (v1, Route B manifest-reuse)."""
    del request
    result = submit_copy(
        body=body,
        owner_id=owner_id,
        owner_name=owner_name,
        bot_repo=bot_repo,
        bot_copy_repo=bot_copy_repo,
        bot_script_service=bot_script_service,
        manifest_seam=manifest_seam,
        passport_plugin=passport_plugin,
        space_context=space_context,
    )
    return Envelope(data=result)


@router.post(
    "/copy/{operation_id}/retry",
    status_code=200,
    response_model=Envelope[RetryResponse],
    responses={404: {"description": "Unknown operation_id"}},
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
    """Re-hand-off a copy operation to create_job (operator-triggered retry)."""
    del request
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


__all__ = [
    "BotCopyRequest",
    "BotCopyResponse",
    "RetryResponse",
    "RetryResult",
    "retry_copy",
    "router",
    "submit_copy",
]
