"""Owner-scoped, one-shot collaboration repair for one Bot.

The address is the Bot and the command is the owner's. Nothing in the request
may name a tenant, environment or delivery-side Bot id: those are read from the
resolved Bot row and the request's verified tenant, so a caller cannot aim the
repair at another deployment or another Bot. The body is empty by contract;
any payload is refused before a plugin is opened.

The owner gate is the existing seam: admission declares
``GRANT_CHECKED_OWN_BOT`` and the handler resolves the Bot exactly as the other
owner-scoped handlers do, with ``get_bot(bot_id, owner_id)``. A caller who is
not the owner gets the same masked 404 as a Bot that does not exist, before
this module reaches the factory.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    resolve_avernet_tenant,
)
from agentclaw.community.adapters.http.openapi_v1.errors_collaboration_repair import (
    COLLABORATION_REPAIR_RESPONSES,
    body_not_accepted,
    repair_error_response,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    require_granted_own_bot,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.schemas_collaboration_repair import (
    CollaborationRepairItem,
    CollaborationRepairReport,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.di.modules.collaboration_repair_module import (
    CollaborationRepairFactory,
)
from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.kernel.collaboration_repair import (
    RepairCommand,
    RepairFactsError,
    RepairPreconditionError,
    RepairReport,
)
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(
    prefix="/openapi/v1/bots",
    tags=["bots"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(require_granted_own_bot)],
)

#: The only request headers the repair delivery may borrow.
_FORWARDED_HEADERS = frozenset({"authorization", "cookie"})


def _profile_headers(request: Request) -> dict[str, str]:
    """The existing authentication context, narrowed and lower-cased.

    The key is normalized so the mapping handed to the factory does not depend
    on how the wire spelled each header.
    """
    return {
        key.lower(): value
        for key, value in request.headers.items()
        if key.lower() in _FORWARDED_HEADERS
    }


def _serialized(report: RepairReport) -> CollaborationRepairReport:
    """Translate the kernel report into the published schema, item by item."""
    return CollaborationRepairReport(
        bot_id=report.bot_id,
        status=report.status,
        items=[
            CollaborationRepairItem(
                component=item.component,
                status=item.status,
                error_code=item.error_code,
            )
            for item in report.items
        ],
    )


@router.post(
    "/{bot_id}/collaboration/repair",
    response_model=Envelope[CollaborationRepairReport],
    responses=COLLABORATION_REPAIR_RESPONSES,
)
@envelope_errors
async def repair_collaboration(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
    request: Request,
    tenant: Annotated[str, Depends(resolve_avernet_tenant)],
    bots: BotServiceProtocol = Injected(BotServiceProtocol),
    factory: CollaborationRepairFactory = Injected(CollaborationRepairFactory),
):
    """Repair this Bot's BCS collaboration metadata from committed Backend facts.

    A 200 reports that the attempt finished, not that every component
    succeeded: data.status and each item's status/error_code carry the real
    outcome.
    """
    # Resolve first, so a non-owner sees the surface's masked 404 rather than a
    # body-validation answer that would confirm the Bot exists.
    bot = bots.get_bot(bot_id, owner_id)

    # A repair takes no business payload. FastAPI would ignore an undeclared
    # body, so the refusal is explicit: nothing in a body may influence the
    # command.
    if await request.body():
        return body_not_accepted(request)

    command = RepairCommand(
        key=SyncKey(tenant, str(bot["env"]), int(bot["id"])),
        actor_user_id=owner_id,
    )

    service_opened = False
    try:
        async with factory.open(_profile_headers(request)) as service:
            service_opened = True
            report = await service.repair(command)
    except (RepairPreconditionError, RepairFactsError) as exc:
        # The raise site disambiguates the shared ``collaboration_sync_unavailable``
        # code: a refused factory is 503, a failed probe after opening is 502.
        return repair_error_response(exc, request, service_opened=service_opened)

    # One bounded outcome line per attempt. Only the finite component/status/
    # code vocabulary is recorded — never the forwarded headers or any payload.
    logger.info(
        "[COLLABORATION-REPAIR] bot_id=%s status=%s items=%s",
        for_log(bot_id),
        report.status,
        ", ".join(
            f"{item.component}/{item.status}/{item.error_code or '-'}"
            for item in report.items
        ),
    )
    return envelope(_serialized(report), request)
