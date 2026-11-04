"""Bots group — ``/openapi/v1/bots`` endpoints.

Public handlers that delegate to the existing internal bot services and wrap the
result in the standard :class:`Envelope` / :class:`Page` contracts. Identity is
the end user the request names in ``?user_id=`` (validated to the verified
caller via ``UserIdDep``); the bot owner a read or write addresses comes from
``OwnerIdDep`` — the caller by default, a shared bot's owner when the request
names one. ``check-name`` asks a tenant-wide question and takes neither. The
request tenant is bound by ``AvernetTenantMiddleware``
before the handler runs, so every service read/write is already tenant-scoped by
the Track A guard. Services are obtained with ``Injected`` exactly as the
internal router does.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    STARTUP_SCRIPT_WRITE_RESPONSES,
    USER_SCOPED_403,
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    StartupScriptUnsupportedError,
)
from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
    require_granted_addressed_bot,
    require_granted_own_bot,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import OwnerIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotOperationNotAllowedError,
)
from agentclaw.community.api.bot_startup_script_service import (
    SUPPORTED,
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.api.data_init_service import DataInitServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


from agentclaw.community.core.services.engine_config import (
    engine_config_coords_from_bot,
)
from .startup_script_support import (
    _startup_script_payload,
    _startup_script_target,
    _withdraw_the_write_if_the_bot_was_deleted,
)
from .schemas import (
    BotQuotaExceededData,
    DataInitRequest,
    DataInitResult,
    StartupScript,
    StartupScriptWrite,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

logger = get_logger()


#: The bot authorization for an application caller, on the own-bot operations
#: of this group.
#:
#: Declared per route here, unlike the four groups that are wholly own-bot and get it
#: at ``include_router``. This group is mixed: it also holds the bots listing
#: (Mode B), the ceiling (C), the name check (OPEN) and bot creation
#: (user-delegated), none of which names a bot — and on those the check would
#: refuse an application outright rather than authorize it. ``admission.py``
#: is the authority on which route is which; ``test_admission_inventory.py``
#: fails if a declaration and a mode disagree.
_GRANT_CHECKED_OWN_BOT = [Depends(require_granted_own_bot)]
_GRANT_CHECKED_ADDRESSED_BOT = [Depends(require_granted_addressed_bot)]

BOT_QUOTA_CONFLICT_RESPONSES = {
    409: {
        "model": Envelope[BotQuotaExceededData],
        "description": "The target Space has no capacity for another Bot.",
    }
}

router = APIRouter(prefix="/openapi/v1/bots", tags=["bots"], route_class=PublicAPIRoute)



def _audit_actor(caller: ActingCaller, owner_id: str) -> str:
    """Who to record as having changed the script.

    For an application caller ``user_id`` is the *delegating* user, not the
    caller — downstream code cannot tell an admitted application from that user,
    which is the seam's whole point. That is right for scoping and wrong for an
    audit field: recording it would have this executable body attributed to a
    person who did not write it. So an application is named as itself, with the
    user it acted for kept alongside.
    """
    if caller.is_application:
        return f"app:{caller.app_id}:on-behalf-of:{owner_id}"
    return owner_id


@router.get(
    "/{bot_id}/startup-script",
    response_model=Envelope[StartupScript],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def get_bot_startup_script(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    startup_script_service: BotStartupScriptServiceProtocol = Injected(
        BotStartupScriptServiceProtocol
    ),
) -> Envelope[StartupScript]:
    """Read a bot's startup script.

    A bot that has never had one reads as an empty script, not an error. An
    unsupported bot still answers here — with supported false and a reason —
    so a caller can discover why before trying to write.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id, state, reason = _startup_script_target(bot, startup_script_service)
    record = startup_script_service.get(entity_id=entity_id, bot_id=bot_id)
    return envelope(_startup_script_payload(bot_id, record, state, reason), request)


@router.put(
    "/{bot_id}/startup-script",
    response_model=Envelope[StartupScript],
    responses=STARTUP_SCRIPT_WRITE_RESPONSES,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def update_bot_startup_script(
    bot_id: BotIdPath,
    body: StartupScriptWrite,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    startup_script_service: BotStartupScriptServiceProtocol = Injected(
        BotStartupScriptServiceProtocol
    ),
) -> Envelope[StartupScript]:
    """Set or replace a bot's startup script.

    Takes effect the next time the platform composes a start command — a
    restart or republish of the bot does that. Lower-level restarts and
    scale-outs reuse the previously composed configuration, so an instance
    started that way can still run the previously stored script until the bot
    is next restarted or republished.

    Refused for a bot whose container cannot run one: storing it would be a
    silent no-op the caller could not distinguish from success.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    # Desktop bots are refused by ``resolve_support`` itself, not by a guard
    # here: gating only the write made GET answer ``supported: true`` for a bot
    # whose next PUT was certain to fail, which is precisely the discovery path
    # GET exists to provide.
    entity_id, state, reason = _startup_script_target(bot, startup_script_service)
    if state != SUPPORTED:
        raise StartupScriptUnsupportedError(reason)
    record = startup_script_service.put(
        entity_id=entity_id,
        bot_id=bot_id,
        script=body.script,
        # From the verified caller, never the body — and naming the application
        # when one is acting, not the user it acted for. The caller, not the
        # addressed owner: an audit column attributing a collaborator's edit
        # to the bot's owner is the one thing an audit column must not do.
        modifier=_audit_actor(caller, user_id),
    )
    _withdraw_the_write_if_the_bot_was_deleted(
        bot_id, entity_id, owner_id, bot_service, startup_script_service
    )
    return envelope(_startup_script_payload(bot_id, record, SUPPORTED, ""), request)


@router.delete(
    "/{bot_id}/startup-script",
    response_model=Envelope[Deleted],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def delete_bot_startup_script(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    startup_script_service: BotStartupScriptServiceProtocol = Injected(
        BotStartupScriptServiceProtocol
    ),
) -> Envelope[Deleted]:
    """Clear a bot's startup script. Idempotent.

    Takes effect the next time the platform composes a start command — see the
    PUT for which starts do and do not recompose. Clearing does not reach an
    already-running container, and does not reach a targeted device restart or
    a scale-out replica until the bot is next restarted or republished.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id = bot.get("entity_id")
    if not entity_id:
        raise BotNotFoundError("bot has no associated entity")
    startup_script_service.delete(entity_id=entity_id, bot_id=bot_id)
    return deleted_envelope(request)


def _require_personal_cloud_bot(bot: dict[str, Any]) -> None:
    bot_type = bot.get("bot_type") or ""
    if bot_type == "desktop":
        raise BotOperationNotAllowedError(
            "local bots do not support data initialization"
        )
    if bot_type == "service":
        raise BotOperationNotAllowedError(
            "service bot data lifecycle is owned by the publish flow"
        )
    if bot_type != "personal":
        raise BotOperationNotAllowedError(
            f"data initialization is not supported for bot_type: {bot_type or 'unknown'}"
        )


def _observe_data_init_task(task: asyncio.Task[dict[str, str]]) -> None:
    """Consume a detached task's exception so failures are never unobserved."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "data-init background task failed",
            exc_info=(type(error), error, error.__traceback__),
        )


@router.get(
    "/{bot_id}/data-init",
    response_model=Envelope[DataInitResult],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def get_bot_data_init_status(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    data_init_service: DataInitServiceProtocol = Injected(DataInitServiceProtocol),
) -> Envelope[DataInitResult]:
    """Read cold-start initialization state without exposing the bot ext bag."""
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard (→ 404)
    _require_personal_cloud_bot(bot)
    result = data_init_service.get_status(bot_id, owner_id)
    return envelope(DataInitResult(**result), request)


@router.post(
    "/{bot_id}/data-init",
    response_model=Envelope[DataInitResult],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def trigger_bot_data_init(
    bot_id: BotIdPath,
    body: DataInitRequest,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    data_init_service: DataInitServiceProtocol = Injected(DataInitServiceProtocol),
) -> Envelope[DataInitResult]:
    """Trigger cold-start data initialization for a personal cloud bot.

    The operation returns immediately while work continues in the background.
    Read this resource with GET to observe the persisted state. Local and
    service bots are refused with 409.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard (→ 404)
    _require_personal_cloud_bot(bot)
    _coords = engine_config_coords_from_bot(bot, bot_id=bot_id, owner_id=owner_id)
    entity_id, entity_type = _coords.entity_id, _coords.entity_type

    # Cookie parsing belongs to the HTTP adapter. The transport-agnostic service
    # decides whether and when the temporary credential must be persisted.
    iam_token = request.cookies.get("IAM_TOKEN") or None
    task = asyncio.create_task(
        data_init_service.trigger_init(
            bot_id=bot_id,
            owner_id=owner_id,
            entity_id=entity_id,
            entity_type=entity_type,
            force=body.force,
            iam_token=iam_token,
        )
    )
    task.add_done_callback(_observe_data_init_task)
    return envelope(
        DataInitResult(
            bot_id=bot_id,
            status="in_progress",
            message="data initialization dispatched",
        ),
        request,
    )
