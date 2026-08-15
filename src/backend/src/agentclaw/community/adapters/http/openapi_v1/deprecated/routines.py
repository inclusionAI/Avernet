"""Legacy routines addresses.

Six of the seven took ``bot_id`` as a required query parameter beside a
``{routine_id}`` path segment, and move the same way resources does.

The seventh is the create, and it is the reason this package needs an
authorization mechanism of its own. It took the bot in the request body, which
``require_granted_bot`` cannot see — the body is not parsed when dependencies
resolve — so the check has to run inside the shim, first, before anything
touches the bot. That is exactly the arrangement ``TODO(#960)`` recorded as a
defect and the new surface no longer has. It survives here because the *old
contract* survives here, and it is deleted when these addresses are.
"""

from __future__ import annotations

from fastapi import Request
from pydantic import ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.routines import (
    router as routines_router,
)
from agentclaw.community.adapters.http.openapi_v1.routines.router import create_routine
from agentclaw.community.adapters.http.openapi_v1.routines.schemas import (
    Routine,
    RoutineSpec,
)
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.di import Injected

from ._relocate import bot_first_to_query, relocate
from ._requery import LegacyBotIdQuery, deprecated_doc, with_query_parameter
from ._shim import legacy_route, legacy_router

_CREATE = "/openapi/v1/bots/{bot_id}/routines"


# It keeps the name, and the replacement took a new one. A component name is
# part of the contract a generated client is written against: leaving
# `RoutineCreate` on the *new* shape would mean a caller who regenerated an SDK
# while still on this address found the type they construct stripped of the
# field they set — a break inside the window that exists to prevent exactly
# that. The name goes when this address does.
#
# The docstring stays plain: it is published as the schema's description.
class RoutineCreate(RoutineSpec):
    """The create body as it was, with the bot named inside it."""

    # Its own example, because ``model_config`` is inherited: with the bot
    # dropped from ``RoutineSpec``'s, this model would publish an example
    # missing the one field it *requires*. The current model has the opposite
    # problem for the opposite reason, which is why neither can borrow the
    # other's.
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260813_a7k2m9p1",
                "name": "morning-brief",
                "trigger": {"type": "schedule", "cron": "0 9 * * 1-5"},
                "command": "Summarize yesterday's tickets and post the brief.",
                "timezone": "Asia/Shanghai",
                "enabled": True,
            }
        }
    )

    bot_id: str = Field(description="The bot that will run the routine.")


def _bot_to_query(endpoint, method, new_path):
    return with_query_parameter(
        endpoint,
        "bot_id",
        LegacyBotIdQuery,
        doc=deprecated_doc(endpoint, f"{method} {new_path}"),
    )


router = relocate(
    routines_router,
    legacy_router("/openapi/v1/bots/routines", "routines"),
    bot_first_to_query("routines"),
    # The create is the one whose bot was in the body, not the query.
    skip=lambda method, path: method == "POST" and path == _CREATE,
    transform=_bot_to_query,
)


async def create_routine_legacy(
    body: RoutineCreate,
    owner_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Routine]:
    """Create a routine on a bot.

    Deprecated: use POST /openapi/v1/bots/{bot_id}/routines, which names the
    bot in the address and is authorized before the handler runs.
    """
    # The bot is in the body, so the shared dependency could not see it and
    # waved this request through. The check is here, and it is the *first*
    # thing: a refusal that arrived after the routine existed would be the
    # worst of both.
    caller.require_bot(body.bot_id, owner_id=owner_id)
    return await create_routine(
        bot_id=body.bot_id,
        body=RoutineSpec(**body.model_dump(exclude={"bot_id"})),
        owner_id=owner_id,
        request=request,
        factory=factory,
    )


#: The create is mounted **without** the shared grant check, on its own router.
#: Not a style choice: require_granted_bot reads the bot off the path or the
#: query string, finds neither here, and *refuses* an application caller rather
#: than deferring — so mounting it with the others would turn a working legacy
#: call into a 404. The old contract kept this operation out of the dependency's
#: reach, so the old address has to as well.
create_router = legacy_router("/openapi/v1/bots/routines", "routines")

legacy_route(
    create_router,
    "POST",
    "",
    create_routine_legacy,
    replaces="/openapi/v1/bots/{bot_id}/routines",
    response_model=Envelope[Routine],
    status_code=201,
    operation_name="create_routine",
)

__all__ = ["create_router", "router"]
