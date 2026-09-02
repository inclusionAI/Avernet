"""User-granted account-level authorizations — ``/openapi/v1/org/user/authorized-apps``.

Three operations over one record (``core/user_app_grant``), the account-level
sibling of the bot-scoped group in ``router.py``, with the same asymmetry in
**which identities each requires**:

- **Granting** needs both parties on the same request: the user's identity and
  the calling application's own credential. The application is never a
  parameter — it is read off the verified principal — so a request cannot
  point a grant at any application but the caller.
- **Listing** and **withdrawing** need only the user. A withdrawal that
  required the application's cooperation would be no withdrawal at all.

**What the record admits.** An application holding a user's account-level
grant is admitted, acting as that user, to every operation ``admission.py``
marks ``USER_GATED`` — the ones that name no bot: the user's quota, routines
across every bot, Spaces, work orders and notifications, local devices. It
confers nothing on any bot; that is the bot-level record's question.

**It sits under the org group, not under bots**, because it is a property of
the user rather than of any bot, and the gateway routes ``/openapi/v1/org/**``
to this backend exactly as it routes ``/openapi/v1/bots/**``. Nothing here is
addressed by a bot id, so the reserved-component list under ``/bots`` does not
grow.

All three are ``REFUSED`` to a machine caller: delegation is a human act, so
an application must not widen its own account-level access, withdraw a
competitor's, or enumerate who else may act as the user. The refusal already
happens centrally in ``require_principal``; declaring it here makes the
decision visible on the group that carries it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.authorized_apps.schemas import (
    UserAuthorizedApp,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_403,
    Deleted,
    Envelope,
    Page,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_user_and_app_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted as deleted_envelope,
    envelope_errors,
    page,
)
from agentclaw.community.api.user_app_grant_service import (
    UserAppGrantRecord,
    UserAppGrantServiceProtocol,
)
from agentclaw.community.core.gateway_principal import PrincipalType
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/openapi/v1/org/user/authorized-apps",
    tags=["authorized-apps"],
    dependencies=[Depends(refuse_app_only_caller)],
    route_class=PublicAPIRoute,
)

#: Both parties. The APP half is checked here; the USER half comes from
#: ``require_principal``, which this depends on. Granting is a consent moment.
UserAndAppDep = Annotated[Principal, Depends(require_user_and_app_principal)]


def _calling_app(principal: Principal) -> tuple[int, str]:
    """The calling application's id and name, off the verified principal.

    Raises :class:`MissingPrincipalError` when the set names no application.
    ``require_user_and_app_principal`` has already refused that case, so this
    is the fail-closed answer for a route that forgot the dependency rather
    than a reachable branch.
    """
    for entry in principal.principals:
        if entry.type == PrincipalType.APP:
            return entry.app.app_id, entry.app.app_name
    raise MissingPrincipalError("no verified application identity on this request")


def _to_user_authorized_app(record: UserAppGrantRecord) -> UserAuthorizedApp:
    return UserAuthorizedApp(
        app_id=record.app_id,
        app_name=record.app_name,
        user_id=record.user_id,
        granted_at=record.gmt_create,
    )


@router.post(
    "",
    status_code=201,
    response_model=Envelope[UserAuthorizedApp],
    responses=USER_SCOPED_403,
)
@envelope_errors
async def grant_user_authorized_app(
    request: Request,
    user_id: UserIdDep,
    principal: UserAndAppDep,
    grants: UserAppGrantServiceProtocol = Injected(UserAppGrantServiceProtocol),
) -> Envelope[UserAuthorizedApp]:
    """Authorize the calling application to act as you at the account level.

    Admits the application, acting as you, to the operations of this API that
    concern your account rather than any one bot — your quota, your routines
    across every bot, your Spaces, work orders and local devices. It grants
    nothing on any bot: a bot is authorized separately, through
    `POST /openapi/v1/bots/{bot_id}/authorized-apps`.

    The application is read from the credential presented, never from a
    parameter. Idempotent: granting an authorization that is already in force
    returns it unchanged rather than failing.
    """
    app_id, app_name = _calling_app(principal)
    record = grants.grant(user_id=user_id, app_id=app_id, app_name=app_name)
    return created(_to_user_authorized_app(record), request)


@router.get(
    "", response_model=Envelope[Page[UserAuthorizedApp]], responses=USER_SCOPED_403
)
@envelope_errors
async def list_user_authorized_apps(
    request: Request,
    user_id: UserIdDep,
    grants: UserAppGrantServiceProtocol = Injected(UserAppGrantServiceProtocol),
) -> Envelope[Page[UserAuthorizedApp]]:
    """Which applications may act as you at the account level.

    Live authorizations only; a withdrawn one does not appear. Requires only
    your own identity, so it never depends on holding any application's key.
    """
    items = [_to_user_authorized_app(r) for r in grants.list_for_user(user_id=user_id)]
    return page(len(items), items, request)


@router.delete(
    "/{app_id}", response_model=Envelope[Deleted], responses=USER_SCOPED_403
)
@envelope_errors
async def revoke_user_authorized_app(
    request: Request,
    user_id: UserIdDep,
    app_id: int = Path(
        ge=1,
        description="The application whose account-level authorization to "
        "withdraw — its `app_id` as listed on your authorized apps.",
    ),
    grants: UserAppGrantServiceProtocol = Injected(UserAppGrantServiceProtocol),
) -> Envelope[Deleted]:
    """Withdraw an application's account-level authorization.

    The application is named in the path rather than read off a principal,
    because a withdrawal must not require the application's cooperation.
    Withdrawing an authorization that does not exist answers 404, distinctly
    from a successful withdrawal. Any bot-level authorizations the application
    holds from you are untouched; withdraw those on the bot.
    """
    grants.revoke(user_id=user_id, app_id=app_id)
    return deleted_envelope(request)
