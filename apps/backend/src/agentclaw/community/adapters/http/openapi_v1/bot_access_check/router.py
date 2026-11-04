"""``GET /openapi/v1/bots/{bot_id}/access`` — transitional, undocumented.

**Transitional.** Built for one integration's interim needs and expected to be
removed; deleting it means this package, its ``ADMISSION`` / ``AUTHORIZATION``
rows, its error mapping and the ``openapi.bot_access_check`` config block.
It is mounted with ``include_in_schema=False`` so it never enters the
published contract.

**Who may call.** Only an application whose ``app_name`` is on the deployment
allowlist (``OpenApiBotAccessCheckConfig``). The route is ``AdmissionMode.OPEN``
and ``NoCheck`` because there is no human on the wire and no Bot grant to
check; the allowlist, checked here before anything is read, is the gate. The
refusal is the same whichever Bot or user is asked about.

**What the answer means.** The level comes from the same
``resolve_operable_permission_level`` the ``/openapi/v1`` access gate
(``bot_access.py``) adjudicates with, so this report and the gate cannot
disagree: ownership, explicit editor rows, the Space-member grant and the
COSEC revocation all apply. Access is per Bot, so there is no stage input.

**Addressing.** ``entity_id`` is optional. Without it the Bot is found by
``bot_id`` alone, which only succeeds when exactly one live Bot carries that id;
an id shared by several Bots is a 409 asking for ``entity_id``, never a guess.

**Failure direction.** No Bot at the address is a real answer —
``has_access=false``. A failed collaborator lookup is not: it propagates to
the app's 500 handler instead of being reported as a ``false`` that never ran.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    BotAccessCheckForbiddenError,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_app_name
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
    resolve_operable_permission_level,
)
from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.di import Injected
from agentclaw.community.di.openapi_bot_access_check_config import (
    OpenApiBotAccessCheckConfig,
)

from .schemas import AccessLevel, BotAccess, RequiredLevel

PrincipalDep = Annotated[Principal, Depends(require_principal)]

router = APIRouter(prefix="/openapi/v1/bots", tags=["Bots"], route_class=PublicAPIRoute)

_REQUIRED = {
    RequiredLevel.MEMBER: PermissionLevel.MEMBER,
    RequiredLevel.ADMIN: PermissionLevel.ADMIN,
    RequiredLevel.OWNER: PermissionLevel.OWNER,
}


@router.get(
    "/{bot_id}/access",
    response_model=Envelope[BotAccess],
    include_in_schema=False,
)
@envelope_errors
async def get_bot_access(
    request: Request,
    bot_id: BotIdPath,
    principal: PrincipalDep,
    user_id: Annotated[str, Query(min_length=1, max_length=256)],
    entity_id: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    required_level: RequiredLevel = RequiredLevel.MEMBER,
    config: OpenApiBotAccessCheckConfig = Injected(OpenApiBotAccessCheckConfig),
    bots: BotRepository = Injected(BotRepository),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> Envelope[BotAccess]:
    """Report whether ``user_id`` reaches the Bot at ``required_level``."""
    app_name = caller_app_name(principal)
    if app_name is None or app_name not in config.app_names:
        raise BotAccessCheckForbiddenError("caller app is not allowlisted")
    level = await asyncio.to_thread(
        _level, bots, collaborators, bot_id=bot_id, entity_id=entity_id, user_id=user_id
    )
    return envelope(
        BotAccess(
            bot_id=bot_id,
            entity_id=entity_id,
            user_id=user_id,
            has_access=level >= _REQUIRED[required_level],
            level=AccessLevel(level.name.lower()),
            required_level=required_level,
        ),
        request,
    )


def _level(
    bots: BotRepository,
    collaborators: CollaboratorServiceProtocol,
    *,
    bot_id: str,
    entity_id: str | None,
    user_id: str,
) -> PermissionLevel:
    """The user's operable level, ``NONE`` only when no Bot is at the address."""
    try:
        context = BotAddressResolver(bots).resolve(
            bot_id=bot_id, entity_id=entity_id, allow_unique_bot_id=True
        )
    except BotAddressNotFoundError:
        return PermissionLevel.NONE
    # BotLookupAmbiguousError (bot_id shared, no entity_id) propagates → 409.
    # Exceptions from the collaborator lookup propagate (→ 500) by design.
    return resolve_operable_permission_level(
        collaborators,
        bot=context.bot,
        user_id=user_id,
        owner_id=context.current_owner_id,
    )


__all__ = ["router"]
