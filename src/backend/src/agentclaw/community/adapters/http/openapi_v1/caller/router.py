"""Caller group — ``GET /openapi/v1/caller``: who the verified caller is.

The one operation whose *answer* is the end user, so it is also the one
user-scoped read that takes no ``user_id`` parameter: a client calls it to
*learn* that value, and requiring the parameter here would make the id a
precondition of discovering it. Everything it returns is read off the verified
principal the gateway signed — nothing comes from the request — so the answer
cannot be steered by the caller.

Who it is for: a browser client (Teamclaw) whose session credential is an
http-only cookie. The cookie authenticates every request — the gateway resolves
it into the signed principal — but the page's own code cannot read it, so the
client has no way to know the id it must thread through the rest of the
surface. It calls this once, caches the identity, and names it everywhere else;
a stale cache fails closed, because ``require_user_id`` refuses a ``user_id``
that no longer matches the credential with a 403.

Why it is ``REFUSED`` for an application acting alone (``admission.py``): an
app-only caller names no end user, so there is nothing to return — and its own
identity question ("which bots may I reach?") is already answered by
``GET /openapi/v1/bots/authorized``. The refusal is declared on the route with
``refuse_app_only_caller``, as every ``REFUSED`` operation declares it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.caller.schemas import CallerIdentity
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.adapters.http.openapi_v1.principal import (
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)

router = APIRouter(prefix="/openapi/v1/caller", tags=["caller"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]

_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]


@router.get(
    "",
    response_model=Envelope[CallerIdentity],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def get_caller_identity(
    request: Request,
    principal: PrincipalDep,
) -> Envelope[CallerIdentity]:
    """Return the end user the caller's credential names.

    The identity the gateway resolved and signed: the id to use as `user_id`
    on every user-scoped operation, plus the profile attributes the identity
    provider supplied. Call it once per session and cache the result — the
    values only change when the session's credential does.
    """
    # ``getattr`` for the same reason ``principal.py``'s helpers read
    # tolerantly: production always supplies a ``VerifiedCaller``, and a test
    # stand-in that models no user must land in the fail-closed branch below
    # rather than on an AttributeError.
    user = getattr(principal, "user", None)
    if user is None:
        # Unreachable behind ``refuse_app_only_caller`` for a verified caller;
        # kept as the fail-closed answer for a hand-constructed principal.
        raise MissingPrincipalError("principal names no end user")
    return envelope(
        CallerIdentity(
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            full_name=user.full_name,
            tenant=principal.tenant,
        ),
        request,
    )
