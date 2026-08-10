"""Owner-granted bot authorizations — ``/openapi/v1`` HTTP adapter.

Four operations over one record, read from both ends, with a deliberate
asymmetry in **which identities each requires**:

- **Granting** needs both parties on the same request: the bot owner's identity
  and the calling application's own credential. That is what makes it a consent
  moment rather than something one side can arrange alone. The application is
  never a parameter — it is read off the verified principal — so a request
  cannot point a grant at any application but the caller.
- **Withdrawing** and **the owner's list** need only the owner. A withdrawal
  that required the application's cooperation would be no withdrawal at all,
  which is precisely the situation it exists for: a credential lost, rotated, or
  a relationship ended. And answering "which apps can reach my bot?" must not
  depend on holding any one application's key.
- **The application's list** needs both again, and here the application is not
  merely required, it is what the answer is *scoped by*. The gateway's auth
  runner resolves only the identities a route declares
  (``gateway/core/authn/_runner.py``), so on a user-only rule the App would
  never reach the signed principal and the query would have nothing to filter
  on. Declaring it is what makes the scoping possible at all.

**Authority is owner-only, and no code here decides it.** Every bot-scoped
operation resolves the bot through ``BotService.get_bot(bot_id, owner_id)``,
which raises ``BotNotFoundError`` when the bot is absent *or* not the caller's.
A non-owner therefore gets the answer a caller naming a nonexistent bot gets,
byte for byte, and the surface never confirms a bot exists to someone who may
not manage it. This is deliberately narrower than the bar for *operating* a bot
(``core/engine_runtime/gate.py`` admits collaborators at member level): handing
a machine credential durable, human-free access to a bot is not the same power
as driving it.

Requiring an application identity is never what authorizes a call — the owner's
identity is. The App is present so the record can name it, not to grant anyone
anything.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from agentclaw.community.adapters.http.openapi_v1.authorized_apps.schemas import (
    AuthorizedApp,
    AuthorizedBot,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_403,
    Deleted,
    Envelope,
    Page,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
    require_user_and_app_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted as deleted_envelope,
    envelope_errors,
    page,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord
from agentclaw.community.core.bot_app_grant.services import BotAppGrantService
from agentclaw.community.core.gateway_principal import PrincipalType
from agentclaw.community.di import Injected

#: The bot-scoped group — the owner's three operations.
router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/authorized-apps", tags=["authorized-apps"]
)

#: The app-scoped read, at its own top-level prefix. A second router rather than
#: a second path on the first: it is not beneath a bot, and mounting it under
#: ``{bot_id}`` would say it was.
app_view_router = APIRouter(
    prefix="/openapi/v1/authorized-bots", tags=["authorized-apps"]
)

#: Both parties. The APP half is checked here; the USER half is guaranteed
#: upstream by ``verify_principal_token``, which admits no identity set naming
#: no end user.
UserAndAppDep = Annotated[Principal, Depends(require_user_and_app_principal)]

#: The owner alone.
PrincipalDep = Annotated[Principal, Depends(require_principal)]

#: Shared by both bot-scoped path declarations so the constraint cannot drift
#: between them.
BotIdPath = Annotated[str, Path(min_length=1, max_length=256)]


def _calling_app(principal: Principal) -> tuple[int, str]:
    """The calling application's id and name, off the verified principal.

    Raises :class:`MissingPrincipalError` when the set names no application.
    ``require_user_and_app_principal`` has already refused that case, so this is
    the fail-closed answer for a route that forgot the dependency rather than a
    reachable branch — it refuses instead of defaulting, because the alternative
    on the app's list view is not a weaker check but an *unscoped* one.
    """
    for entry in principal.principals:
        if entry.type == PrincipalType.APP:
            return entry.app.app_id, entry.app.app_name
    raise MissingPrincipalError("no verified application identity on this request")


def _to_authorized_app(record: BotAppGrantRecord) -> AuthorizedApp:
    return AuthorizedApp(
        app_id=record.app_id,
        app_name=record.app_name,
        bot_id=record.bot_id,
        granted_at=record.gmt_create,
    )


def _to_authorized_bot(record: BotAppGrantRecord) -> AuthorizedBot:
    return AuthorizedBot(bot_id=record.bot_id, granted_at=record.gmt_create)


@router.post("", response_model=Envelope[AuthorizedApp], responses=USER_SCOPED_403)
@envelope_errors
async def grant_authorized_app(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    principal: UserAndAppDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    grants: BotAppGrantService = Injected(BotAppGrantService),
) -> Envelope[AuthorizedApp]:
    """Authorize the calling application to reach this bot.

    Idempotent: granting an authorization that is already in force returns it
    unchanged rather than failing, so a partner retrying a timed-out request is
    not punished for one that actually succeeded.
    """
    # Ownership guard and existence check in one: raises BotNotFoundError -> 404
    # for a bot that is absent or not the caller's, indistinguishably.
    bot_service.get_bot(bot_id, owner_id)
    app_id, app_name = _calling_app(principal)
    record = grants.grant(
        bot_id=bot_id, owner_id=owner_id, app_id=app_id, app_name=app_name
    )
    return created(_to_authorized_app(record), request)


@router.get(
    "", response_model=Envelope[Page[AuthorizedApp]], responses=USER_SCOPED_403
)
@envelope_errors
async def list_authorized_apps(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    grants: BotAppGrantService = Injected(BotAppGrantService),
) -> Envelope[Page[AuthorizedApp]]:
    """The owner's view — which applications can reach this bot."""
    del principal  # authority comes from the owner-scoped bot read below
    bot_service.get_bot(bot_id, owner_id)
    records = grants.list_for_bot(bot_id=bot_id, owner_id=owner_id)
    items = [_to_authorized_app(record) for record in records]
    return page(len(items), items, request)


@router.delete(
    "/{app_id}", response_model=Envelope[Deleted], responses=USER_SCOPED_403
)
@envelope_errors
async def revoke_authorized_app(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    principal: PrincipalDep,
    app_id: int = Path(ge=1),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    grants: BotAppGrantService = Injected(BotAppGrantService),
) -> Envelope[Deleted]:
    """Withdraw an application's authorization for this bot.

    Named in the path rather than read off a principal, because the owner must
    be able to withdraw without the application's cooperation. Withdrawing an
    authorization that does not exist answers 404, distinctly from a successful
    withdrawal (``GrantNotFoundError``).
    """
    del principal
    bot_service.get_bot(bot_id, owner_id)
    grants.revoke(bot_id=bot_id, owner_id=owner_id, app_id=app_id)
    return deleted_envelope(request)


@app_view_router.get(
    "", response_model=Envelope[Page[AuthorizedBot]], responses=USER_SCOPED_403
)
@envelope_errors
async def list_authorized_bots(
    request: Request,
    owner_id: UserIdDep,
    principal: UserAndAppDep,
    grants: BotAppGrantService = Injected(BotAppGrantService),
) -> Envelope[Page[AuthorizedBot]]:
    """The application's view — which of this owner's bots may it reach.

    Names no bot and so performs no bot-existence check, unlike the three above:
    there is nothing to mask. The result is the caller's own authorizations over
    their own bots, and an empty page discloses nothing — which is why holding
    none answers ``200`` with no items rather than ``404``.
    """
    app_id, _ = _calling_app(principal)
    records = grants.list_for_app(app_id=app_id, owner_id=owner_id)
    items = [_to_authorized_bot(record) for record in records]
    return page(len(items), items, request)
