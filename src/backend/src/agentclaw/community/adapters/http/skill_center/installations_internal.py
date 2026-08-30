"""Internal endpoints for running the Installation backfill.

The flush that keeps Installation agreeing with SkillSet configuration is
otherwise only reached lazily, in front of a capability read — so a Bot
converges when something happens to read it. Configuration that reaches many
Bots at once (platform Default-Set content, a direct ``is_active`` fix, a
``center://`` membership resolving to a newly published version) has no
per-Bot write to ride on, and these endpoints are how an operator converges
that fan-out on purpose.

Bearer-token auth, like ``/api/internal/dormant/*``: these run writes across
whole pages of Bots and are not part of any user-facing surface. They are
DB-side only — no device is touched, no runtime projection is triggered — so a
Bot converged here still needs a projection before its engine sees the change.

Like every path outside ``/openapi/v1/*``, these run under the default
``avernet_tenant`` (see ``AvernetTenantMiddleware``), so a page sweep reaches
that tenant's Bots and no other.
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
    BackfillPageData,
    BackfillPageRequest,
    BackfillPageResponse,
)
from agentclaw.community.api.installation_backfill_service import (
    BotBackfillOutcome,
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


def _outcome(outcome: BotBackfillOutcome) -> BackfillOutcomeModel:
    return BackfillOutcomeModel(
        bot_id=outcome.bot_id,
        owner_id=outcome.owner_id,
        changed=outcome.changed,
        error=outcome.error,
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
        outcome = service.backfill_bot(bot_id=body.bot_id, owner_id=body.owner_id)
    except LocalSkillNotFoundError:
        raise HTTPException(status_code=404, detail="Bot not found")
    return BackfillBotResponse(success=True, data=_outcome(outcome))


@internal_router.post("/backfill/page", response_model=BackfillPageResponse)
async def backfill_page(
    body: BackfillPageRequest,
    _: None = Depends(verify_skill_center_internal_token),
    service: InstallationBackfillServiceProtocol = Injected(
        InstallationBackfillServiceProtocol
    ),
) -> BackfillPageResponse:
    """Converge one page of the env's Bots.

    The caller drives the paging: ``total`` and ``has_more`` say whether to
    ask for the next page. A Bot that fails is reported in ``outcomes`` with
    its error and counted in ``failed``; the rest of the page still runs.
    """
    report = service.backfill_page(
        owner_id=body.owner_id,
        engine_type=body.engine_type,
        page=body.page,
        page_size=body.page_size,
    )
    logger.info(
        "[installation_backfill] page done page=%s page_size=%s scanned=%s "
        "changed=%s failed=%s total=%s",
        report.page,
        report.page_size,
        report.scanned,
        report.changed,
        report.failed,
        report.total,
    )
    return BackfillPageResponse(
        success=True,
        data=BackfillPageData(
            total=report.total,
            page=report.page,
            page_size=report.page_size,
            scanned=report.scanned,
            changed=report.changed,
            failed=report.failed,
            has_more=report.has_more,
            outcomes=[_outcome(outcome) for outcome in report.outcomes],
        ),
    )
