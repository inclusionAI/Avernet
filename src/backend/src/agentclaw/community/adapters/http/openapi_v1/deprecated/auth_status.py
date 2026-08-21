"""The retiring GET spelling of the auth-status poll.

The one legacy entry in this package that retires a **method**, not an address:
``GET /openapi/v1/bots/{bot_id}/auth-status`` became a POST at the same path.
The operation was never a read — on the 202 create flow it is what actually
creates the bot once authorization is granted — so it moved to the method that
says so, and its inputs moved from the query string into a request body, where
a create's attributes belong.

The old contract is owned here, whole: the same query parameters with the same
names, descriptions and validation. Both spellings run the same completion
body (``_complete_auth_status``), so they answer identically and cannot drift
apart in what they create — including the one behaviour change shipped with
the method move: a passport service that returns no status yet answers as a
not-ready PENDING on both spellings rather than the 502 this address used to
map it to. A wait must not read as an outage on the address unmigrated callers
are still polling, and diverging the two would make the shared body a lie.

It shares the package's deprecation window (see ``middleware.py``); like every
address here, removal is driven by the access log, not the date.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query, Request

from agentclaw.community.adapters.http.openapi_v1.bots.router import (
    AUTH_STATUS_RESPONSES,
    _complete_auth_status,
)
from agentclaw.community.adapters.http.openapi_v1.bots.schemas import (
    BotAuthStatus,
    BotType,
)
from agentclaw.community.adapters.http.openapi_v1.clusters import ClusterName
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import envelope_errors
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin

from ._requery import deprecated_doc
from ._shim import legacy_route, legacy_router

_REPLACEMENT = "/openapi/v1/bots/{bot_id}/auth-status"


@envelope_errors
async def get_bot_auth_status(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    engine: Annotated[
        str | None,
        Query(
            description="Echo of the engine the bot was requested with. "
            "Required in practice on the 202 flow: creation completes here, "
            "and an omitted value falls back to the deployment default."
        ),
    ] = None,
    # Enum, not a bare str, exactly as the address always published it:
    # validate_engine_cluster accepts only ACRA/ANDC, and a plain string would
    # let a generated client compile a value the server always rejects.
    cluster_name: Annotated[
        ClusterName | None,
        Query(
            description="Echo of the cluster the bot was requested with; "
            "validated against the engine exactly as on create."
        ),
    ] = None,
    bot_name: Annotated[
        str | None,
        Query(description="Echo of the name the bot was requested with."),
    ] = None,
    bot_desc: Annotated[
        str | None,
        Query(description="Echo of the description the bot was requested with."),
    ] = None,
    bot_type: Annotated[
        BotType | None,
        Query(
            description="Echo of the bot type the bot was requested with; "
            "defaults to 'personal' when omitted."
        ),
    ] = None,
    space_id: Annotated[
        str | None,
        Query(description="Business space to associate with the created bot."),
    ] = None,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    auth_rel_plugin: AuthRelationshipPlugin = Injected(AuthRelationshipPlugin),
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
) -> Envelope[BotAuthStatus]:
    """Poll authorization for a pending creation; the bot is created on ISSUED.

    On the 202 create flow the bot is only actually created here, so the
    caller must re-supply the attributes it created with — the optional query
    parameters mirror the create body and are forwarded to completion. Omit
    them and the bot is created with defaults that contradict what was
    requested, so always echo back engine, cluster_name, bot_name, bot_desc
    and bot_type when polling.

    Every restriction create enforces is re-applied to the echoed values:
    the same engine registry check, the same engine/cluster pairing, and the
    same personal/service restriction on bot_type.

    While the authorization service has no status for the bot yet — the
    Passport is not ready — the poll answers PENDING with a message saying
    so, rather than an error: keep polling.
    """
    return _complete_auth_status(
        bot_id=bot_id,
        request=request,
        owner_id=owner_id,
        engine=engine,
        cluster_name=cluster_name,
        bot_name=bot_name,
        bot_desc=bot_desc,
        bot_type=bot_type,
        space_id=space_id,
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        auth_rel_plugin=auth_rel_plugin,
        space_context=space_context,
    )


get_bot_auth_status.__doc__ = deprecated_doc(
    get_bot_auth_status, f"POST {_REPLACEMENT}"
)

router = legacy_router("/openapi/v1/bots", "bots")

legacy_route(
    router,
    "GET",
    "/{bot_id}/auth-status",
    get_bot_auth_status,
    replaces=_REPLACEMENT,
    response_model=Envelope[BotAuthStatus],
    responses=AUTH_STATUS_RESPONSES,
    operation_name="get_bot_auth_status",
)

__all__ = ["router"]
