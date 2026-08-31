"""Internal endpoint for running the Installation backfill on one Bot.

The flush that keeps Installation agreeing with SkillSet configuration is
otherwise only reached lazily, in front of a capability read — so a Bot
converges when something happens to read it. Configuration that reaches many
Bots at once (platform Default-Set content, a direct ``is_active`` fix, a
``center://`` membership resolving to a newly published version) has no
per-Bot write to ride on, and this endpoint is how a backfill converges that
fan-out on purpose.

One Bot per call. Choosing the ``(bot_id, owner_id)`` pairs and pacing the
calls belongs to whoever drives the backfill; this is the tool that driver
invokes, not the driver.

Bearer-token auth, like ``/api/internal/dormant/*``: it writes capability
state and is not part of any user-facing surface. It is DB-side only — no
device is touched, no runtime projection is triggered — so a Bot converged
here still needs a projection before its engine sees the change.

Like every path outside ``/openapi/v1/*``, it runs under the default
``avernet_tenant`` (see ``AvernetTenantMiddleware``), so it reaches that
tenant's Bots and no other.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agentclaw.community.adapters.http.skill_center.internal_auth import (
    verify_skill_center_internal_token,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    BackfillBotRequest,
    BackfillBotResponse,
    BackfillOutcomeModel,
)
from agentclaw.community.api.installation_backfill_service import (
    InstallationBackfillServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

internal_router = APIRouter(
    prefix="/api/internal/skill-center/installations",
    tags=["skill-center-internal"],
)


@internal_router.post("/backfill/bot", response_model=BackfillBotResponse)
async def backfill_bot(
    body: BackfillBotRequest,
    _: None = Depends(verify_skill_center_internal_token),
    service: InstallationBackfillServiceProtocol = Injected(
        InstallationBackfillServiceProtocol
    ),
) -> BackfillBotResponse:
    """Converge one exact Bot.

    404 when the Bot does not exist for that owner — a typo'd id must not
    read as "nothing needed converging".
    """
    logger.info(
        "[installation_backfill] bot request bot_id=%s owner_id=%s",
        body.bot_id,
        body.owner_id,
    )
    try:
        service.backfill_bot(bot_id=body.bot_id, owner_id=body.owner_id)
    except LocalSkillNotFoundError:
        raise HTTPException(status_code=404, detail="Bot not found")
    return BackfillBotResponse(
        success=True,
        data=BackfillOutcomeModel(bot_id=body.bot_id, owner_id=body.owner_id),
    )
