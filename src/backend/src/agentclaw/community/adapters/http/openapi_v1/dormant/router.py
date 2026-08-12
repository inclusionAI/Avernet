"""Public dormant Bot activation route — ``POST /openapi/v1/bots/dormant/{bot_id}/activate``.

Activates a recycled *personal cloud* Bot. Local (desktop) and service Bots are
refused here rather than routed through ``ActivateBotService``:

- A desktop bot is recycled/activated through its own desktop service and BaaS
  container lifecycle; the dormant scan exempts local bots (PRD §10.7), so a
  desktop bot is never in the RECYCLED state this endpoint reactivates from.
- A service bot's offline/online lifecycle is owned by the service line's
  publish flow (``ServiceLifecyclePort`` seam), not by personal/local dormant.

The handler does the owner lookup and ``bot_type`` guard itself, then delegates
only the reactivation orchestration (Passport unfreeze + ``start_bot``) to
``BotDormantActivateServiceProtocol``. ``ActivateBotService.activate`` re-checks
the ``RECYCLED`` state and raises ``InvalidBotStateError`` (→ 409) for a bot
that is not reclaimable, so a race between read and activate still fails closed.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    USER_SCOPED_403,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_dormant_service import (
    BotDormantActivateServiceProtocol,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import (
    BotOperationNotAllowedError,
)
from agentclaw.community.di import Injected

from .schemas import BotActivateResult

router = APIRouter(prefix="/openapi/v1/bots/dormant", tags=["bot-dormant"])


def _require_personal_cloud_bot(bot: dict) -> None:
    """Refuse dormant activation for non-personal-cloud bots (→ 409).

    ``bot_type`` is the only field that distinguishes a personal cloud bot from
    a desktop or service bot at this layer; ``status`` is checked downstream
    by ``ActivateBotService.activate`` (RECYCLED only).
    """
    bot_type = bot.get("bot_type") or ""
    if bot_type == "desktop":
        raise BotOperationNotAllowedError(
            "local bots are not reclaimed by dormant activation"
        )
    if bot_type == "service":
        raise BotOperationNotAllowedError(
            "service bot lifecycle is owned by the publish flow"
        )
    if bot_type != "personal":
        raise BotOperationNotAllowedError(
            f"dormant activation is not supported for bot_type: {bot_type or 'unknown'}"
        )


@router.post(
    "/{bot_id}/activate",
    response_model=Envelope[BotActivateResult],
    responses=USER_SCOPED_403,
)
@envelope_errors
async def activate_dormant_bot(
    bot_id: str,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    activate_service: BotDormantActivateServiceProtocol = Injected(
        BotDormantActivateServiceProtocol
    ),
) -> Envelope[BotActivateResult]:
    """Activate a recycled personal cloud Bot.

    Owner/tenant guard runs first via ``bot_service.get_bot`` (raises
    ``BotNotFoundError`` → 404 for a bot that is not the caller's). The
    ``bot_type`` guard then refuses desktop/service (→ 409) before the
    reactivation orchestration is delegated.
    """
    bot = bot_service.get_bot(bot_id, owner_id)
    _require_personal_cloud_bot(bot)
    result = activate_service.activate(bot_id=bot_id, user_id=owner_id)
    return envelope(
        BotActivateResult(
            bot_id=bot_id,
            status=str(result.get("status") or ""),
            message=result.get("message"),
        ),
        request,
    )
